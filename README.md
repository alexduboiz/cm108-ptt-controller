# CM108 PTT Controller

A simple push-to-talk app for a radio interfaced through a C-Media CM108/CM119
USB sound card, with the PTT circuit wired to **GPIO3**. Audio is handled
separately (e.g. VoiceMeeter).

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

## How it works

The CM108 GPIOs are set with a 5-byte HID output report:
`[0x00, 0x00, output_bits, direction_bits, 0x00]`. GPIO3 is bit `0x04`.
To use another pin, change `PTT_GPIO` in `cm108_ptt.py`.

## License

MIT — see [LICENSE](LICENSE).
