# CM108 PTT Controller

A simple push-to-talk app for a radio interfaced through a C-Media CM108/CM119
USB sound card, with the PTT circuit wired to **GPIO3**. Audio is handled
separately (e.g. VoiceMeeter).

Use your computer headset (or microphone and speakers) as your radio gear:
listen to the radio and talk through it from your PC, with PTT on screen.

## Setup

```
python -m pip install -r requirements.txt
```

## Run

```
python cm108_ptt.py
```

or double-click `run.bat` (no console window).

## Use

- **Hold** the big button (or Spacebar) to transmit; release to stop.
- Check **Toggle mode** to click once for TX on and again for TX off.
- The button turns red and shows **TRANSMITTING** while GPIO3 is high, with a
  timer underneath.
- **TX timeout (seconds, 0 = off)**, default 120: PTT is released
  automatically when the countdown reaches 0:00 (it turns yellow for the last
  10 seconds). With 0, the timer counts up how long you've been transmitting.
  To transmit again after a timeout, release and press PTT again.
- PTT is always released when the window closes, the mode changes, or the
  device is changed.
- Settings (toggle mode, timeout, VoiceMeeter link) are remembered in
  `%APPDATA%\CM108 PTT\settings.json`.

## VoiceMeeter link

With **Link to VoiceMeeter** checked (the default), the app finds the
VoiceMeeter input strip whose device is the CM108 (matched by name) and turns
that strip's **Mute** button into a PTT button:

- Mute the CM108 strip in VoiceMeeter → the radio transmits (and radio receive
  audio is silenced while you talk). Unmute → back to receive.
- The app's PTT button, Spacebar and TX timeout mute/unmute the strip in the
  same way, so both always match.
- When the link starts, the strip is unmuted so the radio never keys by
  surprise.
- It reconnects automatically if VoiceMeeter is started later or restarted.
  Uncheck the option to use the app on its own.

## Known limitations

### Voicemeeter (Standard) — you hear yourself while transmitting

The Standard Voicemeeter has only one hardware bus (**A**) besides the virtual
bus, so the radio and your own audio cannot be routed separately:

- Input 1 = CM108 (radio receive), Input 2 = your microphone
- A1 = CM108 (radio mic input), A2 = your speakers
- Both inputs must be routed to bus **A**

Because A1 and A2 get the same mix, your microphone also reaches your
speakers, so **you hear yourself while PTT is pressed**. Voicemeeter Banana
and Potato have separate A1/A2/A3 buses, which lets you send the microphone
to the CM108 only.

## How it works

The CM108 GPIOs are set with a 5-byte HID output report:
`[0x00, 0x00, output_bits, direction_bits, 0x00]`. GPIO3 is bit `0x04`.
To use another pin, change `PTT_GPIO` in `cm108_ptt.py`.

## License

MIT — see [LICENSE](LICENSE).
