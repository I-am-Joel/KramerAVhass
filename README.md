# Kramer AV for Home Assistant

Control and monitor Kramer matrix and presentation scaler switchers from Home Assistant, with
two-way control: changes made on the unit's front panel appear in Home
Assistant and changes made in Home Assistant are reflected at
the unit.

This is a fork of [krohrbaugh/kramer-homeassistant][original] by Kevin
Rohrbaugh, and it would not exist without his work. See [Credits](#credits).

A newly-built, custom dashboard card for switchers and matrices for use with this integration is at https://github.com/I-am-Joel/kramerav-matrix-panel-card

<img width="1800" height="406" alt="VS-812" src="https://github.com/user-attachments/assets/bf5bd08c-208e-443d-acc4-4c3a5d2bc133" />

I have tested this integration on four units (Kramer VS-162AVRCA, VS-812 & VS-808YC matrix switchers and a Kramer VP-729 presentation scaler), with the matrix  switchers connected through Brainboxes ES-247 and ES-246 serial-to-Ethernet bridges and the presentation scaler directly via ethernet. Other models and bridges should work but have not been tested.

<img width="2654" height="1456" alt="kramer-integration-device-view" src="https://github.com/user-attachments/assets/9ca684f6-f2e3-4c06-bc18-2db16ec647c7" />

## What you get

Depending on the features your device supports, the integration creates one device with one, some or all of:

| Entity | Type | What it does |
| -- | -- | -- |
| Device name (for example "Studio 1 - VS-812") | `media_player` | Choose one input for **all** outputs. Shows **Mixed** when the outputs are not all set to the same input, and **Off** when all are disconnected. |
| All outputs | `select` | The same as the media player, exposed as a simple input list drop-down that sends to all outputs at once. |
| Output 001 Video, Output 002 Video ... | `select` | One per audio and one per video output. Choose which input feeds that output, or **Off** to turn the output off. |
| Mode | `select` | **Audio follows video** or **Breakaway**. |
| Front panel lock | `switch` | Locks or unlocks the buttons on the unit. |
| Status | `sensor` | One diagnostic sensor per unit that holds the whole picture of the unit (see [The Status sensor](#the-status-sensor)). |
| Custom refresh | `switch` | Turns on a refresh interval of your own (see below). Found in the device's **Controls**. |
| Refresh interval | `number` | How often, in seconds (5 to 1800), the integration re-reads the unit's full state. Only available while **Custom refresh** is on. |
| In step | `binary_sensor` | One per output, on models with separate audio. On while that output's audio and video come from the same input. |
| Read only sensors | `sensor`, `binary_sensor` | Numerous sensors are exposed. Depending on what can be read from the unit, these can include firmware version, routing summary, total number of stored presets and connection status. |

All of these update in HASS and on the device front panel, where available. Default naming of inputs and outputs on matrix switchers are
"Input 001 Audio", "Input 002 Video" etc and "Output 001 Audio", "Output 002 Video" etc, until you rename them.

## How it connects

The integration uses Kramer's [Protocol 2000][p2000] for matrix switchers and ASCII for presentation scalers, both over a single TCP
connection. It was built to work via either serial (RS-232) or ethernet.

```
Home Assistant ---- network ---- ethernet-serial bridge>RS-232/direct ethernet ---- Kramer
```

<img width="900" height="640" alt="kramer-integration-add-device-1" src="https://github.com/user-attachments/assets/eb85bd78-3dd1-4ae8-8ee6-be826250a06b" />

The integration:

- keeps a connection open for each device and reconnects by itself if a bridge or the
  switcher loses power or the network drops;
- asks the switcher for its full state when it connects, acts on messages the unit sends as it changes and re-reads full state, defaulted to once per minute;

**Only one program should be connected to the bridge's TCP port at a time.**
Some bridges pass serial data to every connected client but some may
drop connections when two clients are attached. Do not leave a second tool
(for example `nc` or another Home Assistant integration) connected to the same
port as this Kramer AV integration.

## The Status sensor

Each unit has one **Status** sensor (a diagnostic entity, with an entity ID ending `_status`) that holds the whole state of the unit in one place. It is designed for dashboard cards and automations. Its state is `online` while the unit is answering, and it is unavailable otherwise. Everything else is in its attributes:

| Attribute | Meaning |
| -- | -- |
| `schema_version` | Version of this attribute layout (see below). |
| `name`, `model` | The name you gave the device and the model name. |
| `inputs`, `outputs` | Number of inputs and outputs. |
| `features` | What the model supports: `modes`, `audio`, `off`, `lock` (each true or false) and `presets` (the number of presets, 0 if none). |
| `mode` | `afv` or `breakaway`, or empty on models without modes. |
| `input_names`, `audio_input_names` | Video (or only) and audio input names, in order. The audio list is empty on models without separate audio. |
| `output_names`, `audio_output_names` | Video (or only) and audio output names, in order. The audio list is empty on models without separate audio. |
| `video_routes`, `audio_routes` | For each output in order, the number of the input feeding it (0 means off). |
| `presets` | `slots` (how many), `filled` (the preset numbers in use) and `descriptions` (your HASS descriptions). |
| `firmware` | Firmware version, where the unit reports it. |
| `last_error` | The latest error message and the time it happened. |
| `locked` | Whether the front panel is locked, where the model supports it. |

The attributes are not stored in Home Assistant's history, so routing changes do not make the database grow. Only the sensor's state is recorded.

**Stable layout.** Attributes may be added in future versions, but existing ones are never renamed, removed or changed in type. A change that breaks this would raise `schema_version`, so anything you build on this sensor can check that number first.

## Requirements

Home Assistant 2025.1 or later.

## Before you install

1. Confirm that either your Kramer matrix switcher supports Protocol 2000 or your presentation scaler uses ASCII. Kramer units normally use 9600 baud, 8 data bits, no
   parity, 1 stop bit.
2. Set up your ethernet-to-serial bridge (if required) with matching serial settings, in raw TCP
   server mode, and give it a unique, fixed IP address. Note the TCP port it listens on. Otherwise configure the device directly.
3. Check the cable and any null-modem setting if using serial. On the VS-162AV, for instance, a DIP switch on the rear defines whether a null-modem adapter is
   required or a straight-through cable will work. On other models this cannot be defined and you must either use a null-modem cable or a null-modem adaptor with a straight-through cable. If the unit stays silent, this is the first thing to check.
4. Check and set the unit's **machine number**. A single unit is normally number 1. This is usually defined using dip switches. Daisy-chained units will each require a different number (see limitations, below).
5. If HASS or a computer on your network cannot see your bridge or device, try keeping HASS/your computer and the bridge/device on a network path that does not block or
   isolate them from one another. This might be a software configuration on your router or something else. During testing I found TP-Link routers blocked Brainboxes bridges and my VP-729 via ethernet, even though they could be found with a direct network connection from another device. Using a network path that didn't rely on those routers (such as unmanaged network switches) resolved this.
6. This integration is designed to keep its connection with a connected device open at all times. If you need to change connection settings, disable the integration entry in HASS first, as this will ensure there's no conflict with the integration.

## Installation (choose either the HACS or manual method, then proceed to the setup stage)

### HACS (custom repository)

1. In HACS, open the three-dot menu and choose **Custom repositories**.
2. Enter the URL of this repository, choose **Integration**, and add it.
3. Search for the integration in HACS and choose **Download**.
4. Restart Home Assistant.

### Manual

Copy the `custom_components/kramer` folder from this repository into the
`custom_components` folder of your Home Assistant configuration, then restart.

## Setup

1. Go to **Settings > Devices & services > Add integration** and choose your model (if listed).
2. Fill in the setup wizard as required for your device type. The machine number defaults to 1 - make sure your unit is configured to match.
3. For matrix switchers, if your model is not listed, you can choose **Other** and enter its model name, the number of inputs
   and outputs it supports, whether it also supports separate audio and video switching and the number of presets available (if any). You can choose any size of switcher or matrix, up to 127x127 (the maximum supported by Protocol 2000).
4. Home Assistant tests the connection before saving. If it fails, you will be
   told whether it could not connect at all or connected but got no answer from
   the switcher.

### Changing settings later

- **Reconfigure** (three-dot menu on the integration): change available options relevant to your device, including name, address, port, machine number, model and number of supported presets. You cannot use reconfigure to switch between Protocol 2000 and ASCII controlled devices.
- **Configuration** (on the integration):
  Rename audio and video inputs and audio and video outputs, one name per line. Leave a line blank to keep the default. The names "Off" and "Mixed"
  are reserved and all names must be unique.

  Set up, store and recall presets (for models that support this). Support varies between models. You can store and recall presets but cannot delete a preset's configuration. Instead you can clear its name to indicate a preset number is free to be overwritten and you can store a new configuration over the top of an existing preset. The integration identifies and displays preset slots that are occupied or empty.

## Actions (matrix switchers only)

Actions are called from scripts, automations, dashboard buttons or Developer tools → Actions. Each one targets a Kramer device (choose the switcher as the target).

Inputs and outputs can be given by number or by name (names are the ones set under Configure → Input and output names, and are not case sensitive). An input can be given by its video or audio name. An output can be given by its video name, its audio name or its shared name (for example "Output 001", which covers "Output 001 Video" and "Output 001 Audio").

### `kramer.set_route`

Sends one input to one or more outputs.

| Field | Required | Meaning |
| --- | --- | --- |
| `input` | yes | Input number or name. `Off` (or `0`) disconnects the output, on models that can. |
| `outputs` | yes | One or more output numbers or names, or `all`. |
| `layer` | no | `both` (the default), `video` or `audio`. On models with separate audio, `video` or `audio` changes only that layer. Changing `audio` on its own needs the unit to be in Breakaway mode. |

```yaml
action: kramer.set_route
target:
  device_id: <your Kramer device>
data:
  input: "Input 003"
  outputs:
    - "Output 001"
    - "2"
```

All names and numbers are checked before anything is sent. If a command fails part-way, the error lists the outputs already switched.

### `kramer.set_routing_mode`

Sets the unit's routing mode, as the **Mode** select does.

| Field | Required | Meaning |
| --- | --- | --- |
| `mode` | yes | `afv` (audio follow video) or `breakaway` (audio and video routed separately). |

```yaml
action: kramer.set_routing_mode
target:
  device_id: <your Kramer device>
data:
  mode: breakaway
```

Models without routing modes (for example the VS-812) refuse this action with an error, and nothing is sent to the unit.

### `kramer.store_preset`

Stores the present setup (video routing, audio routing and mode) in a preset on the unit. Only on models with presets.

| Field | Required | Meaning |
| --- | --- | --- |
| `preset` | yes | Preset number, or its HASS description. |
| `audio_only` | no | `true` stores only the audio routes (models with separate audio). Default `false`. |
| `confirm_overwrite` | no | Must be `true` to overwrite a filled preset. Default `false`. |

A filled preset is refused unless `confirm_overwrite: true` is set. Presets on the unit can be overwritten but cannot be deleted.

### `kramer.recall_preset`

Recalls a stored setup. A full recall also sets the mode that was stored with it. An empty preset is refused.

| Field | Required | Meaning |
| --- | --- | --- |
| `preset` | yes | Preset number, or its HASS description. |
| `audio_only` | no | `true` recalls only the audio routes (models with separate audio). Default `false`. |

### Notes

- Descriptions are only held in Home Assistant, set under Configure → Presets. They are not stored on the unit.
- Preset numbers match those called via the unit's front panel (eg VS-162AV/RCA: 1 to 60).



## Tested models and features

| Model | Notes |
| -- | -- |
| VS-162AV / VS-162AVRCA | 16 x 16 matrix switching, audio-follow-video and breakaway, 60 presets, panel lock (see limitations). Tested on the RCA version. |
| VS-812 | 8 x 1 input switching. |
| VS-808YC | 8 x 8 matrix switching, AFV, breakaway, 16 presets, panel lock |
| VP-729 | multi-format input switching, scaling, format conversion, PIP, image correction, panel lock |

## Limitations (not all features apply to all models)

- **One unit per entry.** Add the integration once per switcher. Daisy-chained
  units on one connection are not tested or supported by this integration. It might work and machine number is available in the configuration if you want to try.
- **Presets.** It is not possible to delete a preset, once added. You *can* overwrite them.
- **Unannounced configuration changes** Some settings changed through a unit's on-screen menu - rather than via a dedicated panel button - are not announced by some models. An example I've discovered is that changing the front panel lock on the VS-162AV/RCA - via its menu driven control - is not announced, but changing the front panel lock on the VS-808YC - via its dedicated "panel lock" button - *is* announced. This integration refreshes all unit's states every 60 seconds, regardless, so an unannounced change will still appear in Home Assistant, but that could take up to 60 seconds. Changes made via the integration in Home Assistant are not affected by this limitation and the unit responds to such changes immediately. If this matters to you, the refresh interval can be adjusted in the device's options, at the cost of extra network traffic.
- The integration adds no features or functions that aren't already on the device. It follows a connected device's own limitations, as well as its own.

## Planned if possible

- Supporting multiple daisy-chained units on a single connection (may not be worthwhile and/or possible).
- More flexible ways to configure unknown models for use in the integration (hopefully soon).
- A model-definition wizard so users can contribute model profiles to the project through GitHub (for the future).
- More models predefined, if I can access and test them.

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
| "Could not connect" during setup | Wrong IP or port, or the bridge (if used) is not in raw TCP server mode. |
| "Connected, but the switcher did not answer" | Serial settings, cable or null-modem setting or adaptor, or wrong machine number. Check configuration options in the product manual and that dip switches on the unit match your required configuration. On some models the up/down and switch numbers aren't clear. EG VS-812 has switches on the underside with 1 to 4 running from right to left of the unit, as viewed from the front and with "on" being towards the back panel<img width="1200" height="800" alt="Kramer vs-812 underside" src="https://github.com/user-attachments/assets/1a19251d-acca-45bc-bcf6-f86c340d9d69" />Image shows underside of Kramer VS-812 with switch 1 in the "on" position. |
| Entities unavailable | Bridge or switcher is off, or the network is down or disconnected. The integration should recover by itself after the underlying issue is resolved. |
| Connection resets when two or more applications are communicating with the bridge | In testing with a Brainboxes ES-246, reliable operation was confirmed with one application (eg this integration) using the bridge's port and became unreliable with two (eg this integration and ssh). Different bridges or firmware versions may support this better. |
| Serial link not working | It's worth testing the link between your HASS host and the unit, bypassing the integration, to narrow down the cause. Disable the Kramer entry for that unit, then run the command: `(printf '\x05\x80\x81\x81'; sleep 3) \| nc -w 5 IPADDRESS PORT \| od -An -tx1` from a terminal in Home Assistant (replace IPADDRESS and PORT). A working link prints one line of four numbers starting 45. No output means the bridge is reachable but the unit isn't answering: check the serial cable (a null-modem adapter is needed on some models), the bridge's serial settings (9600, 8 data bits, no parity, 1 stop bit, no flow control) and the machine number dip switches on the unit. If it outputs an error such as "Connection refused" or "timed out", instead of numbers, Home Assistant can't reach the bridge at all. In which case, check the bridge is powered and on the network, its IP address and port are correctly configured, that it is set to Raw TCP, and that nothing else is connected to it. Don't forget to re-enable the integration entry, when you're done. |

## Credits

This project is a fork of **[kramer-homeassistant][original] by Kevin Rohrbaugh
([@krohrbaugh][author])**, released under the MIT licence. Kevin wrote the
original integration, including the Home Assistant setup, the release and
checking workflows and the HACS packaging that this fork still uses. He
kindly agreed to this fork being developed.

This fork replaces the way the integration talks to the switcher with new
connection layers written for this project, and adds the entities, setup and
reconfiguration described above. The original relied on the `kessler-av` library;
this version does not.

The revised code was written with Claude.

This integration was not written by, or with the endorsement or support of, Kramer AV.

## Licence

MIT, as the original. The original copyright notice is kept in
[LICENSE](LICENSE).

[original]: https://github.com/krohrbaugh/kramer-homeassistant
[author]: https://github.com/krohrbaugh
[p2000]: https://kramerav.com/downloads/protocols/protocol_2000_rev0_51.pdf
