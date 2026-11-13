# Kramer Matrix Switcher for Home Assistant

Control and monitor a Kramer matrix switcher from Home Assistant, with
two-way status: changes made on the unit's front panel appear in Home
Assistant straight away, and changes made in Home Assistant are reflected at
the unit immediately, too.

This is a fork of [krohrbaugh/kramer-homeassistant][original] by Kevin
Rohrbaugh, and it would not exist without his work. See [Credits](#credits).

> **Status:** early release. I have tested on one unit (a Kramer
> VS-162AVRCA), connected through a Brainboxes ES-257 serial-to-Ethernet
> bridge. Other models and bridges are expected to work but are untested.

## What you get

For each switcher you add, the integration creates one device with:

| Entity | Type | What it does |
| -- | -- | -- |
| Device name (for example "Dining Room Kramer") | `media_player` | Choose one input for **all** outputs. Shows **Mixed** when the outputs are not all set to the same input, and **Off** when all are disconnected. |
| All outputs | `select` | The same as the media player, exposed as a simple input list drop-down that sends to all outputs at once. |
| Output 01, Output 02 ... | `select` | One per output. Choose which input feeds that output, or **Off** to turn the output off. |
| Mode | `select` | **Audio follow video** or **Breakaway** (see [Limitations](#limitations)). |
| Front panel lock | `switch` | Locks or unlocks the buttons on the unit. |

All of these update when someone uses the front panel. Default naming of inputs and outputs are
"Input 01", "Input 02" etc and "Output 01", "Output 02" etc, until you rename them.

## How it connects

The integration uses Kramer's [Protocol 2000][p2000] over a single TCP
connection. It was built to connect via a switcher's serial (RS-232) port, using
a serial-to-Ethernet bridge that is set to a plain ("raw") TCP socket.
Switchers with an Ethernet port that use Protocol 2000 over TCP should
also work, but I've not tested this yet. These instructions assume serial (RS-232) connection via an Ethernet <> serial bridge.

```
Home Assistant ---- network ---- serial-to-Ethernet bridge ---- RS-232 ---- Kramer
```

The integration:

- keeps one connection open and reconnects by itself if the bridge or the
  switcher loses power or the network drops (tested);
- asks the switcher for its full state when it connects and then once a
  minute, and acts on messages the unit sends as it changes;
- marks the entities as unavailable when it cannot reach the unit, then should automatically restore when it can.

**Only one program should be connected to the bridge's TCP port at a time.**
Some bridges pass serial data to every connected client, and older firmware may
drop connections when two clients are attached. Do not leave a second tool
(for example `nc` or another Home Assistant integration) connected to the same
port.

## Before you install

1. Confirm that your Kramer switcher uses **Protocol 2000** and find its
   serial settings. Kramer units normally use 9600 baud, 8 data bits, no
   parity, 1 stop bit.
2. Set up your serial-to-Ethernet bridge with matching serial settings, in raw TCP
   server mode, and give it a fixed IP address. Note the TCP port it listens on.
3. Check the cable and any null-modem setting. On the VS-162AV and
   VS-162AVRCA, a DIP switch on the rear defines whether a null-modem adapter is
   required. If the unit stays silent, this is the first thing to check. Some Kramer units don't offer this switch and you may find a null-modem adapter is or isn't required
4. Check the unit's **machine number**. A single unit is normally number 1. Daisy-chained units will each have a different number (see limitations, below).
5. Keep Home Assistant and the bridge on a network path that does not block or
   isolate the devices from one other.

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
3. If your model is not listed, choose **Other** and enter the number of inputs
   and outputs it supports and whether it also supports separate audio and video switching.
   You can choose any size of switcher or matrix, up to 64x64.
4. Home Assistant tests the connection before saving. If it fails, you will be
   told whether it could not connect at all or connected but got no answer from
   the switcher.

### Changing settings later

- **Reconfigure** (three-dot menu on the integration): change the name, address,
  port, machine number or model.
- **Configure** (on the integration): rename the inputs and outputs, one name
  per line. Leave a line blank to keep the default. The names "Off" and "Mixed"
  are reserved and input names must all be different.

## Tested models

| Model | Notes |
| -- | -- |
| VS-162AV / VS-162AVRCA | 16 x 16, audio-follow-video and breakaway. Tested on the RCA version. |
| Other | Enter the number of inputs and outputs yourself. |

More models can be added to `custom_components/kramer/models.py`.

## Limitations

- **Mode.** In testing, my unit only reported whether it is in audio-follow-video or
  breakaway mode. I could not get it to report whether VIDEO or AUDIO is selected on the
  front panel and I was unable to get my test unit to select AUDIO. Therefore, in Breakaway
  mode, route commands from Home Assistant change **video only**; audio keeps
  following whatever it was last set to. Changing the audio separately is not
  yet supported, although I hope to figure this out another time.
- **Matrix format is not read from the unit.** In testing, my VS-162AV did not report its own
  number of inputs and outputs, so the matrix format is defined either by a selected preset models (currently only the VS-162) or via manual entry during setup.
- **Front-panel lock.** The toggles on and off via the integration on my VS-162, but I couldn't see any indicator of its status on the front panel or display of the unit.
- **One unit per entry.** Add the integration once per switcher. Daisy-chained
  units using different machine numbers on one connection are not yet - and may never be - handled
  as a group.

## Planned if possible

- Separate audio routing in Breakaway mode.
- Storing and recalling a unit's preset configuration.
- Supporting multiple daisy-chained units on a single connection.
- More models predefined if I can access and test them and an easier way for users to define those that aren't.

## Troubleshooting

To see everything the integration sends and receives, run this in **Tools (previously Developer tools) > Actions** (YAML mode):

```yaml
action: logger.set_level
data:
  custom_components.kramer: debug
```

Use `warning` in place of `debug` to turn it off. The setting lasts until Home
Assistant restarts. The log shows each connected unit's four-byte messages in hex.

| Symptom | Likely cause |
| -- | -- |
| "Could not connect" during setup | Wrong IP or port, or the bridge is not in raw TCP server mode. |
| "Connected, but the switcher did not answer" | Serial settings, cable or null-modem setting or adaptor, or wrong machine number. Check dip switches on the unit match required configuration. On some models the up/down and switch numbers aren't clear. EG VS-812 where switches are on the underside and 1 to 4 run from right to left of the unit, as viewed from the front, with "on" being towards the back<img width="1200" height="800" alt="vs-821-underside" src="https://github.com/user-attachments/assets/1a19251d-acca-45bc-bcf6-f86c340d9d69" />Image shows underside of Kramer VS-812 with switch 1 in the "on" position. |
| Entities unavailable | Bridge or switcher is off, or the network is down. This should recover by itself when the issue is resolved. |
| Connection resets when two or more applications are communicating with the bridge | In testing with a Brainboxes ES-246, reliable operation was confirmed with one application using the bridge's port and became unreliable with two. Other bridges may support this better. |

## Credits

This project is a fork of **[kramer-homeassistant][original] by Kevin Rohrbaugh
([@krohrbaugh][author])**, released under the MIT licence. Kevin wrote the
original integration, including the Home Assistant setup, the release and
checking workflows and the HACS packaging that this fork still uses, and
kindly agreed to this fork being developed.

This fork replaces the way the integration talks to the switcher with a new
connection layer written for this project, and adds the entities, setup and
reconfiguration described above. The original relied on the `kessler-av` library;
this version does not.

The revised code was written with Claude.

## Licence

MIT, as the original. The original copyright notice is kept in
[LICENSE](LICENSE).

[original]: https://github.com/krohrbaugh/kramer-homeassistant
[author]: https://github.com/krohrbaugh
[p2000]: https://kramerav.com/downloads/protocols/protocol_2000_rev0_51.pdf
