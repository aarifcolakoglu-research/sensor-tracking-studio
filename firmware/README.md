# Firmware

Teensy sketches for the 12-channel sensor array read by Sensor Tracking Studio.
Build with the Arduino IDE and Teensyduino (board **Teensy 4.1**, USB type **Serial**);
no extra libraries are needed beyond `Wire`.

| Sketch | Purpose |
|---|---|
| `helmet_12_sensor_array/` | Firmware used for all helmet measurements: 12 MLX90393 sensors on three I2C buses (A = `Wire`, B = `Wire1`, C = `Wire2`; addresses `0x0C`–`0x0F` on each) |
| `tools/i2c_scanner/` | Lists every device that answers on `Wire` (SDA 18, SCL 19) |
| `tools/mlx90393_address_check/` | Checks that the four MLX90393 addresses `0x0C`–`0x0F` acknowledge on `Wire` |

## Serial format

115200 baud. After start-up:

```
Sensor 0xC of bus A init: OK
...
BOOT:READY
Sensor_Id,Raw_Bx,Raw_By,Raw_Bz,Status
A-1,-314,-1778,7061,2
A-2,...
```

`inference/teensy_serial.py` reads the socket (`A-1` → `A1`) from the first field and
`Raw_Bz` from the fourth; other lines (init messages, header) are skipped. A sensor that
does not answer is sent as `0,0,0,0`.

The sensor registers (`0x00 = 0x005C`, `0x02 = 0x02B4`) are the same as in the
single-sensor calibration firmware
([tactile-sensor-calibration-rig](https://github.com/aarifcolakoglu-research/tactile-sensor-calibration-rig)),
so the raw Bz values read here are on the same scale as the calibration equations.

One pass over the 12 sensors takes about 120 ms, i.e. about 8 Hz per sensor
(median frame interval in the helmet logs: 121 ms).
