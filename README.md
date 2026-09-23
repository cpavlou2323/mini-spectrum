# Mini spectrum

A tiny always-on-top spectrum analyser for whatever your Windows PC is playing: Spotify, YouTube, games, anything. It sits in a corner of your screen as a strip of pixel bars with peak caps, or turns into a glowing X-Y oscilloscope.

It listens to your speaker output through Windows' loopback, so there's nothing to set up and nothing is recorded or saved.

## Using it

Double-click **MiniSpectrum.exe**. The widget appears in the bottom-right corner of your screen.

| To do this | Do this |
| --- | --- |
| Move it | Drag it |
| Switch between thick bars, thin bars, waveform and oscilloscope | Click it |
| Make it bigger or smaller | Scroll the mouse wheel over it |
| Change fall speed, peak caps or always-on-top | Right-click it |
| Close it | Right-click it and choose **Quit** |

[![Download](https://img.shields.io/github/v/release/cpavlou2323/mini-spectrum?label=Download%20for%20Windows&style=for-the-badge)](https://github.com/cpavlou2323/mini-spectrum/releases/latest/download/MiniSpectrum.exe)

### Oscilloscope mode

This works like an analogue oscilloscope in X-Y mode: the left channel moves the beam sideways and the right channel moves it up and down, and the trace glows and fades like real phosphor. Ordinary music makes shifting, tangled shapes. Search YouTube for "oscilloscope music" and those tracks draw actual pictures. When nothing is playing, it shows a resting dot in the middle.

The beam is brighter where it moves slowly, fades like phosphor, and sits behind an 8 x 10 division graticule on a rounded screen. Right-click and pick **Scope colour** for cyan, green, amber, ice or violet. **Scope gain** is normally Auto, which sizes the picture to whatever is playing. The fixed settings work like a volts-per-division knob, where 1× means a full-scale signal fills the screen. Music sits well below full scale, and Windows' volume slider lowers it further, so 10× or 20× is usually where a fixed setting starts to look right.

Sound enhancements such as spatial sound or equaliser apps change the left and right channels, so turn them off if pictures look scrambled.

If you plug in headphones or switch speakers, right-click the widget and choose **Reconnect audio**.

Your settings and the widget's position are saved in `.mini_spectrum.json` in your user folder.

### "Windows protected your PC"

The app isn't signed by a paid certificate, so Windows SmartScreen warns about it the first time. Click **More info**, then **Run anyway**. If your antivirus quarantines it, that's a known false alarm with apps built by PyInstaller; you can also run it from the Python source instead (see below).

## Making the .exe

On a Windows PC with Python installed, double-click **build_exe.bat**. It installs what it needs and builds `dist\MiniSpectrum.exe`. That single file is all anyone else needs, and they don't need Python.

If you put this folder on GitHub, the included workflow can build the .exe for you: open the **Actions** tab, choose **Build Windows app** and click **Run workflow**. Publishing a release (for example `v1.0.0`) builds the .exe and attaches it to that release automatically, so people can download it from your Releases page and the website's download button.

## Running from Python instead

For people who already have Python 3.9 or newer, run this inside this folder:

```
python -m pip install .
```

Then start it with `mini-spectrum`, or with `python -m mini_spectrum` if that command isn't found.

## Website

The `docs` folder holds the project's web page. To publish it, go to your repository's **Settings > Pages**, choose **Deploy from a branch**, pick the **main** branch and the **/docs** folder, and save. The page fills in your GitHub links automatically.

## Requirements

Windows 10 or 11. It uses Windows' WASAPI loopback, so it doesn't run on macOS or Linux.

## Author

Made by Christos P.

## License

MIT. See `LICENSE`.
