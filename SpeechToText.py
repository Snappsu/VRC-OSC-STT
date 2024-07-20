import pyaudio
import asyncio
import json
import os
import sys
import websockets
import threading, datetime, os, time
from tinyoscquery.queryservice import OSCQueryService, OSCAccess
from tinyoscquery.query import OSCQueryBrowser, OSCQueryClient
from tinyoscquery.utility import get_open_tcp_port, get_open_udp_port
from pythonosc import udp_client
from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import BlockingOSCUDPServer
from yaml import load
try:
    from yaml import CLoader as Loader
except ImportError:
    from yaml import Loader

startTime = datetime.datetime.now()

all_mic_data = []
all_transcripts = []

FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000
CHUNK = 8000

audio_queue = asyncio.Queue()

config = {
    'FollowMicMute': True, 
    'CapturedLanguage': "en-US", 
    'TranscriptionMethod': "Google", 
    'TranscriptionRateLimit': 1200,
    'EnableTranslation': False, 
    'TranslateMethod': "Google", 
    'TranslateToken': "", 
    "TranslateTo": "en-US", 
    'AllowOSCControl': True, 
    'Pause': False, 
    'TranslateInterumResults': True,
    }
state = {'selfMuted': False}
state_lock = threading.Lock()


'''
STATE MANAGEMENT
This should be thread safe
'''
def get_state(key):
    global state, state_lock
    state_lock.acquire()
    result = None
    if key in state:
        result = state[key]
    state_lock.release()
    return result

def set_state(key, value):
    global state, state_lock
    state_lock.acquire()
    state[key] = value
    state_lock.release()

'''
OSC BLOCK
TODO: This maybe should be bundled into a class
'''
class OSCServer():
    def __init__(self):
        global config

        self.osc_port = get_open_udp_port()
        self.http_port = get_open_tcp_port()
        self.oscquery = OSCQueryService("VRCSubs-%i" % self.osc_port, self.http_port, self.osc_port)
        print("[OSCQuery] Running on HTTP port", self.http_port, "and UDP port", self.osc_port)

        self.dispatcher = Dispatcher()
        self.dispatcher.set_default_handler(self._def_osc_dispatch)
        self.dispatcher.map("/avatar/parameters/MuteSelf", self._osc_muteself)

        # Set up the OSCQuery params -- VRChat will only automatically send if we advertise that we receive
        self.oscquery.advertise_endpoint("/avatar/parameters/MuteSelf",  get_state('selfMuted'), OSCAccess.WRITEONLY_VALUE)

        
        
        for key in config.keys():
            self.dispatcher.map(f"/avatar/parameters/vrcsub-{key}", self._osc_updateconf)
            self.oscquery.advertise_endpoint(f"/avatar/parameters/vrcsub-{key}", config[key], OSCAccess.WRITEONLY_VALUE)

        browser = OSCQueryBrowser()
        time.sleep(2)
        service = browser.find_service_by_name("VRChat-Client")
        
        if service is not None:
            oscq = OSCQueryClient(service)
            mute_self_node = oscq.query_node("/avatar/parameters/MuteSelf")
            
            # For safety, let's check if selfMuted actually exists (like below) before calling this
            # Some people on discord have this return None sometimes, maybe before VRC inits OSCQ?
            # May warrant further investigation...
            if mute_self_node is not None:
                set_state('selfMuted', mute_self_node.value[0])

            for key in config.keys():
                n = oscq.query_node(f"/avatar/parameters/vrcsub-{key}")
                if n is not None:
                    print(f"[OSCQuery] Config {key} -> {n.value[0]}")
                    config[key] = n.value[0]
            
            print("[OSCQuery] Loaded values from VRChat!")

        self.server = BlockingOSCUDPServer(("127.0.0.1", self.osc_port), self.dispatcher)
        self.server_thread = threading.Thread(target=self._process_osc)

        

    def launch(self):
        self.server_thread.start()

    def shutdown(self):
        self.server.shutdown()
        self.server_thread.join()

    def _osc_muteself(self, address, *args):
        print("[OSCThread] Mute is now", args[0])
        set_state("selfMuted", args[0])

    def _osc_updateconf(self, address, *args):
        key = address.split("vrcsub-")[1]
        print("[OSCThread]", key, "is now", args[0])
        config[key] = args[0]

    def _def_osc_dispatch(self, address, *args):
        pass
        #print(f"{address}: {args}")

    def _process_osc(self):
        print("[OSCThread] Launching OSC server thread!")
        self.server.serve_forever()

'''
SOUND PROCESSING THREAD
'''
def process_sound(text,final):
    client = udp_client.SimpleUDPClient("127.0.0.1", 9000)
    print(1)
    
    client.send_message("/chatbox/input", [text, True])

def mic_callback(input_data, frame_count, time_info, status_flag):
    audio_queue.put_nowait(input_data)
    return (input_data, pyaudio.paContinue)

async def run():
    method = "mic"
    deepgram_url ='wss://api.deepgram.com/v1/listen?model=nova-2&punctuate=true&encoding=linear16&sample_rate=16000'

    # Connect to the real-time streaming endpoint, attaching our credentials.
    async with websockets.connect(
        deepgram_url, extra_headers={"Authorization": "Token {}".format(DEEPGRAM_API_KEY)}
    ) as ws:
        print(f'ℹ️  Request ID: {ws.response_headers.get("dg-request-id")}')
        
        print("🟢 (1/5) Successfully opened Deepgram streaming connection")

        async def sender(ws):
            print("🟢 (2/5) Ready to stream mic audio to Deepgram")
            try:
                #TODO and not get_state("selfMuted")
                while True:
                    mic_data = await audio_queue.get()
                    all_mic_data.append(mic_data)
                    await ws.send(mic_data)
            except websockets.exceptions.ConnectionClosedOK:
                await ws.send(json.dumps({"type": "CloseStream"}))
                print(
                    "🟢 (5/5) Successfully closed Deepgram connection, waiting for final transcripts if necessary"
                )

            except Exception as e:
                print(f"Error while sending: {str(e)}")
                raise

            return

        async def receiver(ws):
            """Print out the messages received from the server."""
            first_message = True
            first_transcript = True
            transcript = ""

            async for msg in ws:
                res = json.loads(msg)
                if first_message:
                    print(
                        "🟢 (3/5) Successfully receiving Deepgram messages, waiting for finalized transcription..."
                    )
                    first_message = False
                try:
                    # handle local server messages
                    if res.get("msg"):
                        print(res["msg"])
                    if res.get("is_final"):
                        transcript = (
                            res.get("channel", {})
                            .get("alternatives", [{}])[0]
                            .get("transcript", "")
                        )
                        
                        if transcript != "":
                            if first_transcript:
                                print("🟢 (4/5) Began receiving transcription")
                                # if using webvtt, print out header
                                if format == "vtt":
                                    print("WEBVTT\n")
                                first_transcript = False
                            print(transcript)
                            final = res.get("is_final")
                            process_sound(transcript,final)
                            all_transcripts.append(transcript)
                    
                except KeyError:
                    print(f"🔴 ERROR: Received unexpected API response! {msg}")

        # Set up microphone if streaming from mic
        async def microphone():
            audio = pyaudio.PyAudio()
            stream = audio.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=RATE,
                input=True,
                frames_per_buffer=CHUNK,
                stream_callback=mic_callback,
            )

            stream.start_stream()

            global SAMPLE_SIZE
            SAMPLE_SIZE = audio.get_sample_size(FORMAT)

            while stream.is_active():
                await asyncio.sleep(0.1)

            stream.stop_stream()
            stream.close()

        functions = [
            asyncio.ensure_future(sender(ws)),
            asyncio.ensure_future(receiver(ws)),
        ]

        if method == "mic":
            functions.append(asyncio.ensure_future(microphone()))

        await asyncio.gather(*functions)

def main():

        
    global config
    # Load config
    cfgfile = f"{os.path.dirname(os.path.realpath(__file__))}/Config.yml"
    if os.path.exists(cfgfile):
        print("[VRCSubs] Loading config from", cfgfile)
        new_config = None
        with open(cfgfile, 'r') as f:
            new_config = load(f, Loader=Loader)
        if new_config is not None:
            for key in new_config:
                config[key] = new_config[key]
    
    osc = None
    launchOSC = False
    if config['FollowMicMute']:
        print("[VRCSubs] FollowMicMute is enabled in the config, speech recognition will pause when your mic is muted in-game!")
        launchOSC = True
    else:
        print("[VRCSubs] FollowMicMute is NOT enabled in the config, speech recognition will work even while muted in-game!")

    if config['AllowOSCControl']:
        print("[VRCSubs] AllowOSCControl is enabled in the config, will listen for OSC controls!")
        launchOSC = True

    if launchOSC:
        osc = OSCServer()
        osc.launch()

    if osc is not None:
        osc.shutdown()
    


    """Entrypoint for the example."""
    # Parse the command-line arguments.

    try:
        asyncio.run(run())

    except websockets.exceptions.InvalidStatusCode as e:
        print(f'🔴 ERROR: Could not connect to Deepgram! {e.headers.get("dg-error")}')
        print(
            f'🔴 Please contact Deepgram Support (developers@deepgram.com) with request ID {e.headers.get("dg-request-id")}'
        )
        return
    except websockets.exceptions.ConnectionClosedError as e:
        error_description = f"Unknown websocket error."
        print(
            f"🔴 ERROR: Deepgram connection unexpectedly closed with code {e.code} and payload {e.reason}"
        )

        if e.reason == "DATA-0000":
            error_description = "The payload cannot be decoded as audio. It is either not audio data or is a codec unsupported by Deepgram."
        elif e.reason == "NET-0000":
            error_description = "The service has not transmitted a Text frame to the client within the timeout window. This may indicate an issue internally in Deepgram's systems or could be due to Deepgram not receiving enough audio data to transcribe a frame."
        elif e.reason == "NET-0001":
            error_description = "The service has not received a Binary frame from the client within the timeout window. This may indicate an internal issue in Deepgram's systems, the client's systems, or the network connecting them."

        print(f"🔴 {error_description}")
        # TODO: update with link to streaming troubleshooting page once available
        # print(f'🔴 Refer to our troubleshooting suggestions: ')
        print(
            f"🔴 Please contact Deepgram Support (developers@deepgram.com) with the request ID listed above."
        )
        return
    except websockets.exceptions.ConnectionClosedOK:
        return
    except Exception as e:
        print(f"🔴 ERROR: Something went wrong! {e}")
        return
    

if __name__ == "__main__":
    sys.exit(main() or 0)
