## Actions

Actions are called from scripts, automations, dashboard buttons or Developer tools → Actions. Each one targets a Kramer device (choose the switcher as the target).

Inputs and outputs can be given by number or by name (names are the ones set under Configure → Input and output names, and are not case sensitive).

### `kramer.set_route`

Sends one input to one or more outputs.

| Field | Required | Meaning |
| --- | --- | --- |
| `input` | yes | Input number or name. `Off` (or `0`) disconnects the output, on models that can. |
| `outputs` | yes | One or more output numbers or names, or `all`. |

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

### `kramer.store_preset`

Stores the present setup (video routing, audio routing and mode) in a slot on the unit. Only on models with stored setups.

| Field | Required | Meaning |
| --- | --- | --- |
| `slot` | yes | Slot number, or its HASS description. |
| `audio_only` | no | `true` stores only the audio routes (models with separate audio). Default `false`. |
| `confirm_overwrite` | no | Must be `true` to overwrite an occupied slot. Default `false`. |

An occupied slot is refused unless `confirm_overwrite: true` is set. Presets on the unit can be overwritten but cannot be deleted.

### `kramer.recall_preset`

Recalls a stored setup. A full recall also sets the mode that was stored with it. A blank slot is refused.

| Field | Required | Meaning |
| --- | --- | --- |
| `slot` | yes | Slot number, or its HASS description. |
| `audio_only` | no | `true` recalls only the audio routes (models with separate audio). Default `false`. |

### Notes

- Anyone who can call actions in Home Assistant can use these. Only the Configure dialogue is limited to administrators.
- Descriptions are held in Home Assistant, set under Configure → Presets, and are not stored on the unit.
- Slots are numbered as on the unit's front panel (VS-162AV/RCA: 1 to 60).
