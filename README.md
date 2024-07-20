# VRC-OSC-STT
My personal stt script for vrchat osc api

OSC is based off of [vrc-osc-scripts](https://github.com/cyberkitsune/)
STT service is deepgram and requires and api key. Deepgram is a paid service that I'm using because the [word error rate](https://www.rev.com/blog/resources/what-is-wer-what-does-word-error-rate-mean) is low, it recognizes very important words (like "snitty"), and it has a very flexable api. 

If you are looking for a STT for VRC that's free, I recommend checking out the vrc-osc-scripts repository above.

Also I've only used this on windows so far. If it works for you or you made it work feel free to lmk 💖

# Setup
1. In the same directory with `SpeechToText.py`, create a file called `DG_API_KEY`.
2. In this file, enter your Deepgram API key
3. Ensure OSC is enabled on VRChat
4. Make sure that your default mic is the mic that you want too transcibe from 
5. Run `SpeechToText.py` and let it run you see `🟢 (2/5) Ready to stream mic audio to Deepgram` in the terminal.
6. You're all set.
