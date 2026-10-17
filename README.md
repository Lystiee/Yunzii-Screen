# Yunzii Screen

A small Windows desktop app tailored for managing pictures and animations on the YUNZII B75 PRO MAX's built-in screen. It communicates directly with the keyboard over USB HID and does not depend on the official web driver.

Yunzii Screen is an independent community project. It is not affiliated with, endorsed by, or sponsored by YUNZII or the keyboard's manufacturer. YUNZII and related product names are trademarks of their respective owner.

## Run on Windows 10/11

1. Install Python 3.10 or newer. During setup, enable **Add Python to PATH**.
2. Double-click `install.bat` to install the Python dependencies.
3. Connect the keyboard, then double-click `start.bat`.
4. Choose an image or GIF, check the screen size and HID report ID, then upload it.

The interface is built with Python's included Tkinter. The only installed packages are Pillow (image and GIF processing) and hidapi (USB HID communication).

## Build a standalone executable

Run `build.bat` after `install.bat`. It installs the build tools in the app's `.venv` and creates `dist\Yunzii Screen.exe`, which bundles Python and the app dependencies, plus `dist\LICENSE.txt`. You can run the executable on a Windows PC without separately installing Python. Keep the license file with the executable when sharing it. The build requires an internet connection to download PyInstaller and its dependencies.

## Features

- Detect compatible Yunzii keyboard display interfaces and select between connected devices.
- Upload static images or GIF animations, with fit/fill sizing.
- Adjust still-image brightness from 0% to 200% before upload; GIF animation frames are not brightness-adjusted.
- Convert frames to the keyboard's RGB565 pixel format.
- Choose a GIF destination: the GIF page (up to 160 frames) or the power-on animation (up to 64 frames); choose playback speed (1–60 FPS).
- Append pictures or explicitly replace all picture slots; clear all images, the GIF-page animation, or the power-on animation independently; switch display pages.
- Sync the keyboard's date and time from the PC's local clock.
- Edit screen dimensions and report ID for models not listed in the known-device table.
- Dark desktop theme with pink button hover highlights and a fixed-size image preview.

## Choosing and uploading files

The **Image or GIF** section only chooses a file and displays a local preview; it does not send anything to the keyboard. For a still image, use **Image brightness** to adjust the image itself from 0% (black) to 200% (twice as bright); 100% leaves it unchanged. This is not the keyboard backlight control. The preview reflects the adjustment and **Upload image** applies it before sending the image to the keyboard. GIF uploads are not brightness-adjusted. Click **Upload image** to store the selected still image, or **Upload GIF** to store its animation. The keyboard has one animation slot per destination: each new GIF uploaded to the GIF page replaces the previous GIF-page animation, and each new startup GIF replaces the previous startup animation. Standard images work differently: each upload without **Replace existing picture(s)** checked is added as another picture slot, so repeated clicks can create duplicate entries. Check **Replace existing picture(s)** to clear all 16 picture slots before uploading one image instead.

The app is tailored for the YUNZII B75 PRO MAX's 160 × 96 screen. **Sync time from PC** sends the computer's current local time and date to the keyboard; make sure Windows' clock and time zone are set correctly first. The power-on animation option uses the boot-animation command described by the yunlcd protocol reference. Firmware support may vary by keyboard model; if your keyboard's firmware does not support boot animations, use the GIF page destination instead.

The device stores uploaded pictures and animations in its flash memory. Avoid frequent uploads. Screen size defaults to 240 × 135 for unknown product IDs; confirm or change it for your model before uploading.

## GIF destinations

Use **GIF destination** to choose where the selected animation is stored:

- **GIF page** stores the animation on the keyboard's GIF page and supports up to 160 frames.
- **Power-on animation** stores the animation for keyboard startup and supports up to 64 frames.

To set a new startup GIF, choose the GIF file, select **Power-on animation** as the destination, then click **Upload GIF**. This writes the animation to the startup slot; it does not switch the screen to the GIF page. The new startup animation is used on a subsequent power-on.

**Clear GIF** clears only the GIF-page animation. **Clear startup GIF** clears only the power-on animation; the selected upload destination does not affect either clear button.

## Compatibility

The app discovers HID display interfaces with vendor IDs `0x28E9` and `0x379A` and the Yunzii display usage page/usage. The YUNZII B75 PRO MAX is listed as product ID `12744` (`0x31C8`), with a `160 × 96` screen and HID report ID `0`. Other known product IDs use dimensions and report IDs collected by the reference project. Unknown models default to an editable `240 × 135` screen and report ID `6`. If detection fails or uploads time out, check whether the official driver or another keyboard utility is currently using the device, and verify the screen size/report ID.

## License and protocol attribution

This project is MIT licensed. Its HID packet format, command values, supported-device data, and image conversion approach are adapted from [yunlcd](https://codeberg.org/snokatt/yunlcd), copyright Daniel Fischer, also licensed under the MIT License. See [LICENSE](LICENSE) for the required license text. Copyright notices identify the software contributors and do not imply affiliation with or endorsement by the YUNZII brand or manufacturer.
