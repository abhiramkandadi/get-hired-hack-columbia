# Meshtastic radio presets used by CHRP Mesh Messenger

These numbers come from the Meshtastic modem presets. The app computes airtime with the Semtech LoRa time-on-air formula.

Short Turbo: spreading factor 7, bandwidth 500 kHz, data rate 21.88 kbps.
Short Fast: spreading factor 7, bandwidth 250 kHz, data rate 10.94 kbps.
Medium Fast: spreading factor 9, bandwidth 250 kHz, data rate 3.52 kbps.
Long Fast: spreading factor 11, bandwidth 250 kHz, data rate 1.07 kbps.
Long Moderate: spreading factor 11, bandwidth 125 kHz, data rate 0.34 kbps.

The demo uses the Medium Fast preset.

A LoRa packet is 230 bytes at most. The header is included in that size.
