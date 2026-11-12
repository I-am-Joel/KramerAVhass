# Kramer Matrix Switcher for Home Assistant

Control and monitor a Kramer matrix switcher from Home Assistant, with
two-way status: changes made on the unit's front panel appear in Home
Assistant straight away, and changes made in Home Assistant are read back from
the unit.

This is a fork of [krohrbaugh/kramer-homeassistant][original] by Kevin
Rohrbaugh, and it would not exist without his work. See [Credits](#credits).

> **Status:** early release. It has been tested on one unit (a Kramer
> VS-162AVRCA, which is electrically the same as the VS-162AV apart from its
> audio sockets) connected through a Brainboxes ES-257 serial-to-Ethernet
> bridge. Other models and bridges are expected to work but are untested.

## What you get

For each switcher you add, the integration creates one device with:

| Entity | Type | What it does |
| -- | -- | -- |
| Device name (for example "Dining Room Kramer") | `media_player` | Choose one input for **all** outputs. Shows **Mixed** when the outputs are not all on the same input, and **Off** when all are disconnected. |
| All outputs | `select` | The same as the media player, as a simple drop-down. |
| Output 01, Output 02 ... | `select` | One per output. Choose which input feeds that output, or **Off** to disconnect it. |
| Mode | `select` | **Audio follow video** or **Breakaway** (see [Limitations](#limitations)). |
| Front panel lock | `switch` | Locks or unlocks the buttons on the unit. |

All of these update when someone uses the front panel. Inputs are named
"Input 01" and so on, and outputs "Output 01" and so on, until you rename them.

## How it connects

The integration talks Kramer's [Protocol 2000][p2000] over a single TCP
connection. It is built for switchers with only a serial (RS-232) port, using
a serial-to-Ethernet bridge that is set to a plain ("raw") TCP socket.
Switchers with their own Ethernet port that speak Protocol 2000 over TCP should
also work, but this has not been tested.

```
Home Assistant ---- network ---- serial-to-Ethernet bridge ---- RS-232 ---- Kramer
```

The integration:

- keeps one connection open and reconnects by itself if the bridge or the
  switcher loses power or the network drops (tested);
- asks the switcher for its full state when it connects and then once a
  minute, and acts on messages the unit sends as it changes;
- marks the entities as unavailable while it cannot reach the unit.

**Only one program should be connected to the bridge's TCP port at a time.**
Some bridges pass serial data to every connected client, and older firmware may
drop connections when two clients are attached. Do not leave a second tool
(for example `nc` or another Home Assistant integration) connected to the same
port.

## Before you install

1. Check in your switcher's manual that it uses **Protocol 2000** and find its
   serial settings. Kramer units normally use 9600 baud, 8 data bits, no
   parity, 1 stop bit.
2. Set up your serial-to-Ethernet bridge with those serial settings, in raw TCP
   server mode, and give it a fixed IP address. Note the TCP port it listens on.
3. Check the cable and any null-modem setting. On the VS-162AV and
   VS-162AVRCA, a DIP switch on the rear selects whether a null-modem adapter is
   needed. If the unit stays silent, this is the first thing to check.
4. Check the unit's **machine number**. A single unit is normally number 1.
5. Keep Home Assistant and the bridge on a network path that does not block or
   isolate devices from each other.

## Installation

### HACS (custom repository)

1. In HACS, open the three-dot menu and choose **Custom repositories**.
2. Enter the URL of this repository, choose **Integration**, and add it.
3. Search for the integration in HACS and choose **Download**.
4. Restart Home Assistant.

### Manual

Copy the `custom_components/kramer` folder from this repository into the
`custom_components` folder of your Home Assistant configuration, then restart.

## Setup

1. Go to **Settings > Devices & services > Add integration** and choose
   **Kramer Matrix Switcher**.
2. Enter a name, the IP address and port of your bridge, the machine number and
   the model.
3. If your model is not listed, choose **Other** and enter its number of inputs
   and outputs, and whether it supports separate audio and video switching.
   You can choose any size up to 64 by 64.
4. Home Assistant tests the connection before saving. If it fails, you will be
   told whether it could not connect at all or connected but got no answer from
   the switcher.

### Changing settings later

- **Reconfigure** (three-dot menu on the integration): change the name, address,
  port, machine number or model.
- **Configure** (on the integration): rename the inputs and outputs, one name
  per line. Leave a line blank to keep the default. The names "Off" and "Mixed"
  are reserved, and input names must all be different.

## Supported models

| Model | Notes |
| -- | -- |
| VS-162AV / VS-162AVRCA | 16 x 16, audio-follow-video and breakaway. Tested on the RCA version. |
| Other | Enter the number of inputs and outputs yourself. |

More models can be added to `custom_components/kramer/models.py`.

## Limitations

- **Mode.** The unit reports only whether it is in audio-follow-video or
  breakaway mode. It does not report whether VIDEO or AUDIO is selected on the
  front panel, and it did not accept a command to select AUDIO. In Breakaway
  mode, route commands from Home Assistant change **video only**; audio keeps
  following whatever it was last set to. Changing the audio separately is not
  yet supported.
- **Size is not read from the unit.** The VS-162AV does not report its own
  number of inputs and outputs, so the model (or your "Other" entry) decides.
- **Front-panel lock.** The lock works, but the unit shows no indicator on its
  display.
- **One unit per entry.** Add the integration once per switcher. Daisy-chained
  units using different machine numbers on one connection are not yet handled
  as a group.

## Planned

- Separate audio routing in Breakaway mode.
- Storing and recalling the unit's presets.
- Daisy-chained units on one connection.
- More models, and an easier way to describe an unlisted model.

## Troubleshooting

To see everything the integration sends and receives, run this in **Developer
tools > Actions** (YAML mode):

```yaml
action: logger.set_level
data:
  custom_components.kramer: debug
```

Use `warning` in place of `debug` to turn it off. The setting lasts until Home
Assistant restarts. The log shows each four-byte message in hex.

| Symptom | Likely cause |
| -- | -- |
| "Could not connect" during setup | Wrong IP or port, or the bridge is not in raw TCP server mode. |
| "Connected, but the switcher did not answer" | Serial settings, cable or null-modem setting, or wrong machine number. |
| Entities unavailable | Bridge or switcher is off, or the network is down. They recover by themselves. |
| Connection resets with two programs attached | Only one client should use the bridge's port. |

## Credits

This project is a fork of **[kramer-homeassistant][original] by Kevin Rohrbaugh
([@krohrbaugh][author])**, released under the MIT licence. Kevin wrote the
original integration, including the Home Assistant setup, the release and
checking workflows and the HACS packaging that this fork still uses, and
kindly agreed to this fork being developed. His original integration is the
right choice for Kramer switches with a network port that only need to be
controlled, not monitored.

This fork replaces the way the integration talks to the switcher with a new
connection layer written for this project, and adds the entities, setup and
reconfiguration described above. The original used the `kessler-av` library;
this version does not depend on it.

The code was written with the help of Claude (an AI assistant made by
Anthropic) and tested by the maintainer on real hardware.

## Licence

MIT, as the original. The original copyright notice is kept in
[LICENSE](LICENSE).

[original]: https://github.com/krohrbaugh/kramer-homeassistant
[author]: https://github.com/krohrbaugh
[p2000]: https://kramerav.com/downloads/protocols/protocol_2000_rev0_51.pdf
