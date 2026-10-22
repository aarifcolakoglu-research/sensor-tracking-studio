/* ===========================================================================
   Helmet 12-sensor array firmware (Teensy 4.1)

   Reads 12 MLX90393 Hall-effect sensors on three I2C buses and streams one CSV
   line per sensor over USB serial to Sensor Tracking Studio.

   Wiring : bus A = Wire  (SDA 18, SCL 19)  -> sockets A-1 .. A-4
            bus B = Wire1 (SDA 17, SCL 16)  -> sockets B-1 .. B-4
            bus C = Wire2 (SDA 25, SCL 24)  -> sockets C-1 .. C-4
            on each bus the four sensors use addresses 0x0C, 0x0D, 0x0E, 0x0F
   Sensor : register 0x00 = 0x005C (GAIN_SEL 5), register 0x02 = 0x02B4;
            identical to the single-sensor calibration firmware, so raw Bz
            values are on the same scale as the calibration data
   Serial : 115200 baud. After start-up the board prints one init line per
            sensor, then "BOOT:READY" and the header
                Sensor_Id,Raw_Bx,Raw_By,Raw_Bz,Status
            followed by lines such as  A-1,-314,-1778,7061,2
            A sensor that does not respond is reported as  A-1,0,0,0,0
   Timing : each sensor is triggered, read 10 ms later, then the next one;
            one pass over the 12 sensors takes about 120 ms (about 8 Hz per
            sensor). SAMPLE_PERIOD_MS is a lower bound on the pass interval.

   Used for all helmet force measurements in the study (Arduino IDE with
   Teensyduino, board "Teensy 4.1", USB type "Serial").
   =========================================================================== */
#include <Arduino.h>
#include <Wire.h>

// Addresses: 1..4  -> 0x0C..0x0F
static const uint8_t ADDR[4] = {0x0C, 0x0D, 0x0E, 0x0F};

// Buses: A, B, C  -> Wire, Wire1, Wire2
static TwoWire* BUSES[3] = { &Wire, &Wire1, &Wire2 };
static const char BUS_LABEL[3] = { 'A', 'B', 'C' };

// Timing
static const uint32_t SAMPLE_PERIOD_MS = 100;  // overall pacing (not per-sensor)

// ----- MLX90393 helpers -----

// Write a 4-byte "write register" command (0x60) and read 1-byte status
static bool mlx_write_reg_and_read_status(TwoWire& w, uint8_t addr,
                                          uint8_t ah, uint8_t al, uint8_t reg,
                                          uint8_t* status_out)
{
  w.beginTransmission(addr);
  w.write(0x60);   // Write register command
  w.write(ah);
  w.write(al);
  w.write(reg);
  uint8_t e = w.endTransmission();
  if (e != 0) {
    if (status_out) *status_out = 0;
    return false;
  }

  // Read back 1-byte status (device returns status after many commands)
  uint8_t n = w.requestFrom(addr, (uint8_t)1);
  if (n != 1 || w.available() != 1) {
    if (status_out) *status_out = 0;
    return false;
  }

  if (status_out) *status_out = (uint8_t)w.read();
  return true;
}

// Start single measurement (ZYX enabled) : 0x3E
static bool mlx_start_single_zyx(TwoWire& w, uint8_t addr, uint8_t* status_out)
{
  w.beginTransmission(addr);
  w.write(0x3E); // Start single measurement, ZYX
  uint8_t e = w.endTransmission();
  if (e != 0) {
    if (status_out) *status_out = 0;
    return false;
  }

  // Read 1-byte status
  uint8_t n = w.requestFrom(addr, (uint8_t)1);
  if (n != 1 || w.available() != 1) {
    if (status_out) *status_out = 0;
    return false;
  }

  if (status_out) *status_out = (uint8_t)w.read();
  return true;
}

// Read measurement (ZYX enabled) : 0x4E
// Expected return: status + X(msb,lsb) + Y(msb,lsb) + Z(msb,lsb) = 7 bytes
static bool mlx_read_meas_zyx(TwoWire& w, uint8_t addr,
                             int16_t* x, int16_t* y, int16_t* z,
                             uint8_t* status_out)
{
  w.beginTransmission(addr);
  w.write(0x4E); // Read measurement, ZYX
  uint8_t e = w.endTransmission();
  if (e != 0) {
    if (status_out) *status_out = 0;
    return false;
  }

  uint8_t n = w.requestFrom(addr, (uint8_t)7);
  if (n != 7 || w.available() != 7) {
    if (status_out) *status_out = 0;
    return false;
  }

  uint8_t b0 = (uint8_t)w.read(); // status
  uint8_t xh = (uint8_t)w.read();
  uint8_t xl = (uint8_t)w.read();
  uint8_t yh = (uint8_t)w.read();
  uint8_t yl = (uint8_t)w.read();
  uint8_t zh = (uint8_t)w.read();
  uint8_t zl = (uint8_t)w.read();

  if (status_out) *status_out = b0;

  if (x) *x = (int16_t)((xh << 8) | xl);
  if (y) *y = (int16_t)((yh << 8) | yl);
  if (z) *z = (int16_t)((zh << 8) | zl);

  return true;
}

// Configure one sensor on one bus
static bool mlx_init_sensor(TwoWire& w, uint8_t addr)
{
  uint8_t st = 0;

  // Register 0x00: AH=0x00, AL=0x5C (GAIN_SEL = 5), address byte (0x00<<2)=0x00
  if (!mlx_write_reg_and_read_status(w, addr, 0x00, 0x5C, 0x00, &st)) return false;

  // Register 0x02: AH=0x02, AL=0xB4 (resolution / filter), address byte (0x02<<2)=0x08
  if (!mlx_write_reg_and_read_status(w, addr, 0x02, 0xB4, 0x08, &st)) return false;

  return true;
}

// Print one CSV line
static void print_line(char busLabel, uint8_t sensorIndex1based,
                       int16_t bx, int16_t by, int16_t bz, uint8_t status)
{
  Serial.print(busLabel);
  Serial.print("-");
  Serial.print(sensorIndex1based);
  Serial.print(",");
  Serial.print(bx);
  Serial.print(",");
  Serial.print(by);
  Serial.print(",");
  Serial.print(bz);
  Serial.print(",");
  Serial.println(status);
}

void setup()
{
  Serial.begin(115200);

  // Start I2C buses
  Wire.begin();
  Wire1.begin();
  Wire2.begin();

  // Optional: speed-up I2C (if wiring + pull-ups support it)
  Wire.setClock(400000);
  Wire1.setClock(400000);
  Wire2.setClock(400000);

  // Init all sensors on all buses
  for (int b = 0; b < 3; b++) {
    for (int i = 0; i < 4; i++) {
      bool ok = mlx_init_sensor(*BUSES[b], ADDR[i]);
      Serial.print("Sensor 0x");
      Serial.print(ADDR[i], HEX);
      Serial.print(" of bus ");
      Serial.print(BUS_LABEL[b]);
      Serial.print(" init: ");
      Serial.println(ok ? "OK" : "FAIL");
      delay(5);
    }
  }
  Serial.println("==================================================================================================================================");
  Serial.println("BOOT:READY");
  Serial.println("Sensor_Id,Raw_Bx,Raw_By,Raw_Bz,Status");
}

void loop()
{
  static uint32_t last_ms = 0;
  uint32_t now = millis();
  if (now - last_ms < SAMPLE_PERIOD_MS) return;
  last_ms = now;

  for (int b = 0; b < 3; b++) {
    TwoWire& w = *BUSES[b];

    for (int i = 0; i < 4; i++) {
      uint8_t addr = ADDR[i];

      // 1) trigger measurement
      uint8_t st1 = 0;
      bool ok1 = mlx_start_single_zyx(w, addr, &st1);
      if (!ok1) {
        print_line(BUS_LABEL[b], (uint8_t)(i + 1), 0, 0, 0, 0);
        continue;
      }

      // Conversion time (about 7 ms for these settings)
      delay(10);

      // 2) read measurement
      int16_t bx = 0, by = 0, bz = 0;
      uint8_t st2 = 0;
      bool ok2 = mlx_read_meas_zyx(w, addr, &bx, &by, &bz, &st2);

      if (!ok2) {
        print_line(BUS_LABEL[b], (uint8_t)(i + 1), 0, 0, 0, 0);
        continue;
      }

      // Use read-measurement status (st2) as the line Status
      print_line(BUS_LABEL[b], (uint8_t)(i + 1), bx, by, bz, st2);
    }
  }
}
