import time
from lcd_api import LcdApi

MASK_RS = 0x01
MASK_RW = 0x02
MASK_E  = 0x04

SHIFT_BACKLIGHT = 3
SHIFT_DATA = 4


class I2cLcd(LcdApi):

    def __init__(self, i2c, i2c_addr, num_lines, num_columns):

        self.i2c = i2c
        self.i2c_addr = i2c_addr

        self.i2c.writeto(self.i2c_addr, bytes([0]))

        time.sleep_ms(20)

        self.hal_write_init_nibble(0x30)
        time.sleep_ms(5)

        self.hal_write_init_nibble(0x30)
        time.sleep_ms(1)

        self.hal_write_init_nibble(0x30)
        time.sleep_ms(1)

        self.hal_write_init_nibble(0x20)
        time.sleep_ms(1)

        LcdApi.__init__(self, num_lines, num_columns)

        self.hal_write_command(
            self.LCD_FUNCTION | self.LCD_FUNCTION_2LINES
        )

    def hal_write_init_nibble(self, nibble):
        byte = (
            (nibble >> 4) << SHIFT_DATA
            | (1 << SHIFT_BACKLIGHT)
        )

        self.i2c.writeto(
            self.i2c_addr,
            bytes([byte | MASK_E])
        )

        self.i2c.writeto(
            self.i2c_addr,
            bytes([byte])
        )

    def hal_write_command(self, cmd):
        byte = (
            ((cmd >> 4) & 0x0F) << SHIFT_DATA
            | (1 << SHIFT_BACKLIGHT)
        )

        self.i2c.writeto(
            self.i2c_addr,
            bytes([byte | MASK_E])
        )

        self.i2c.writeto(
            self.i2c_addr,
            bytes([byte])
        )

        byte = (
            (cmd & 0x0F) << SHIFT_DATA
            | (1 << SHIFT_BACKLIGHT)
        )

        self.i2c.writeto(
            self.i2c_addr,
            bytes([byte | MASK_E])
        )

        self.i2c.writeto(
            self.i2c_addr,
            bytes([byte])
        )

        if cmd <= 3:
            time.sleep_ms(5)

    def hal_write_data(self, data):
        byte = (
            MASK_RS
            | ((data >> 4) & 0x0F) << SHIFT_DATA
            | (1 << SHIFT_BACKLIGHT)
        )

        self.i2c.writeto(
            self.i2c_addr,
            bytes([byte | MASK_E])
        )

        self.i2c.writeto(
            self.i2c_addr,
            bytes([byte])
        )

        byte = (
            MASK_RS
            | (data & 0x0F) << SHIFT_DATA
            | (1 << SHIFT_BACKLIGHT)
        )

        self.i2c.writeto(
            self.i2c_addr,
            bytes([byte | MASK_E])
        )

        self.i2c.writeto(
            self.i2c_addr,
            bytes([byte])
        )