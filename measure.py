#!/usr/bin/python3
import sys
import csv
import argparse
import time
import datetime
import curses
from abc import ABC, abstractmethod
from enum import Enum
from dataclasses import dataclass
from lib.koradserial import KoradSerial
from apa102_pi.driver import apa102

VOLTAGE_ABSOLUTE_MAX = 5.5
COLOR_BITSHIFTS = [16, 8, 0] # RGB

class Default:
    def __init__(self, value):
        self.value = value

    def __float__(self):
        return float(self.value)

class ColorRange(ABC):
    def __init__(self, value_range):
        self.value_range = value_range
        super().__init__()

    @abstractmethod
    def __iter__(self):
        pass
    
    @abstractmethod
    def __len__(self):
        pass

class IndividualColorsRange(ColorRange):
    def __iter__(self):
        for color_bitshift in COLOR_BITSHIFTS:
            for cur_value in self.value_range:
                yield (cur_value << color_bitshift)

    def __len__(self):
        return len(self.value_range) * len(COLOR_BITSHIFTS)

class WhiteOnlyRange(ColorRange):
    def __iter__(self):
        for cur_value in self.value_range:
            yield ((cur_value << COLOR_BITSHIFTS[0]) | 
                   (cur_value << COLOR_BITSHIFTS[1]) |
                   (cur_value << COLOR_BITSHIFTS[2]))
    
    def __len__(self):
        return len(self.value_range)

class FullRainbowRange(ColorRange):
    def __iter__(self):
        for red in self.value_range:
            for green in self.value_range:
                for blue in self.value_range:
                    yield ((red << COLOR_BITSHIFTS[0]) | 
                           (green << COLOR_BITSHIFTS[1]) |
                           (blue << COLOR_BITSHIFTS[2]))
    
    def __len__(self):
        return pow(len(self.value_range), 3)

class Mode(Enum):
    INDIVIDUAL = 0
    WHITE = 1
    FULL = 2

    def color_range(self, value_range):
        options = { 
            Mode.INDIVIDUAL: IndividualColorsRange,
            Mode.WHITE:      WhiteOnlyRange,
            Mode.FULL:       FullRainbowRange,
        }
        return options[self](value_range)

@dataclass
class Context:
    def __init__(self, stdscr, csv_writer, strip, psu_channel, num_leds, settle_time, current_offset):
        self.stdscr = stdscr
        self.csv_writer = csv_writer
        self.strip = strip
        self.psu_channel = psu_channel
        self.num_leds = num_leds
        self.settle_time = settle_time
        self.current_offset = current_offset

class RangeInc:
    def __init__(self, start, stop, step):
        if stop < start:
            raise ValueError("Invalid range: stop must be greater than or equal to start")
        if float(step) <= 0.0:
            raise ValueError("step must be greater than 0")

        self.start = start
        self.stop = stop
        self.step = step

    def __iter__(self):
        value = self.start
        while (value < self.stop):
            yield value
            value += self.step
        yield self.stop

    def __len__(self):
        return int((self.stop - self.start) // self.step) + 1

def get_red(rgb_color):
    return int((rgb_color & 0xFF0000) >> 16)
  
def get_green(rgb_color):
    return int((rgb_color & 0x00FF00) >> 8)
    
def get_blue(rgb_color):
    return int(rgb_color & 0x0000FF)

def set_strip_color(strip, num_leds, red, green, blue, global_brightness):
    # Set color of each LED
    strip.global_brightness = global_brightness
    for led in range(0, num_leds):
        strip.set_pixel(led, red, green, blue)
    
    # Program strip
    strip.show()

def do_measurement(ctx, voltage_setpoint, brightness, red, green, blue):
    # Set voltage
    if voltage_setpoint is not None:
        ctx.psu_channel.voltage = voltage_setpoint

    # Set strip color
    set_strip_color(ctx.strip, ctx.num_leds, red, green, blue, brightness)
    
    # Wait for current to stabilize
    time.sleep(ctx.settle_time)
    
    # Measure voltage and current
    voltage = ctx.psu_channel.output_voltage
    current = (ctx.psu_channel.output_current * 1000.0 / ctx.num_leds) - ctx.current_offset
    
    # Write measurement to file
    ctx.csv_writer.writerow([brightness, red, green, blue, voltage, current])
    return (voltage, current)

def run_measurement_iteration(ctx, voltage_setpoint, brightness, rgb_color, i, nb_iterations, start_time):
    # Calculate RGB color
    red = get_red(rgb_color)
    green = get_green(rgb_color)
    blue = get_blue(rgb_color)

    # Run measurement
    (measured_voltage, measured_current) = do_measurement(ctx, voltage_setpoint, brightness, red, green, blue)

    # Progress calculations
    progress = i / nb_iterations * 100
    time_elapsed = datetime.datetime.now() - start_time
    time_per_iteration = time_elapsed / i
    time_remaining = (nb_iterations - i) * time_per_iteration
    hours_remaining, remainder = divmod(time_remaining.total_seconds(), 3600)
    minutes_remaining, seconds_remaining = divmod(remainder, 60)

    # Print status
    voltage_setpoint_str = 'N/A' if voltage_setpoint is None else '{:4.2f} V'.format(voltage_setpoint)
    ctx.stdscr.addstr(2, 0, f'Voltage setpoint: {voltage_setpoint_str}')
    ctx.stdscr.addstr(3, 0, f'Color: R{red:<3} G{green:<3} B{blue:<3}\t'
                            f'Brightness: {brightness:2}/31')
    ctx.stdscr.addstr(4, 0, f'Voltage: {measured_voltage:4.2f} V\t\t'
                            f'Current: {measured_current:5.2f} mA')
    ctx.stdscr.addstr(5, 0, f'Progress: {progress:6.2f}%\t'
                            f'Time remaining: {int(hours_remaining):2}:{int(minutes_remaining):02}:{int(seconds_remaining):02}')
    ctx.stdscr.refresh()

def run_measurements(ctx, mode, brightness_range, value_range, voltage_range):
    # Write header
    ctx.csv_writer.writerow(['Brightness (31)', 'Red (255)', 'Green (255)', 'Blue (255)', 'Voltage (V)', 'Current (mA)'])

    i = 0
    color_range = mode.color_range(value_range) 
    nb_iterations = len(voltage_range) * len(brightness_range) * len(color_range)
    start_time = datetime.datetime.now()
    for voltage in voltage_range:
        for brightness in brightness_range:
            for rgb_color in color_range:
                i += 1
                run_measurement_iteration(ctx, voltage, brightness, rgb_color, i, nb_iterations, start_time)

def run(args, stdscr):
    mode_str = args.mode
    psu_port = args.psu_port
    psu_channel_no = int(args.psu_channel)
    mosi_pin = args.strip_mosi
    sclk_pin = args.strip_sclk
    rgb_order = args.strip_rgb_order
    current_offset = int(args.current_offset)
    
    num_leds = int(args.num_leds)
    brightness_range = RangeInc(int(args.min_brightness), int(args.max_brightness), int(args.brightness_step))
    value_range = RangeInc(int(args.min_value), int(args.max_value), int(args.value_step))
    settle_time = int(args.settle_time_ms) / 1000.0
   
    if isinstance(args.max_voltage, Default):
        args.max_voltage = args.min_voltage
    elif isinstance(args.min_voltage, Default):
        args.min_voltage = args.max_voltage
    if isinstance(args.min_voltage, Default):
        voltage_range = [None]
    else:
        voltage_range = RangeInc(float(args.min_voltage), float(args.max_voltage), float(args.voltage_step))

    if float(args.max_voltage) > VOLTAGE_ABSOLUTE_MAX:
        raise ValueError("Max voltage out of range")

    if mode_str.upper() not in map(lambda enum: enum.name, list(Mode)):
        raise ValueError("Invalid mode specified")
    mode = Mode[mode_str.upper()]

    with open(args.output_file, mode='w' if args.force else 'x') as csv_file:
        strip = apa102.APA102(num_led=num_leds, mosi=mosi_pin, sclk=sclk_pin, order=rgb_order)
        try:
            with KoradSerial(psu_port) as power_supply:
                stdscr.addstr(0, 0, 'PSU model: {}'.format(power_supply.model))
                stdscr.refresh()
    
                csv_file.write('# Command:, {}\r\n'.format(" ".join(sys.argv[:])))
                csv_file.write('# PSU model:, {}\r\n'.format(power_supply.model))
                csv_file.write('\r\n')
    
                csv_writer = csv.writer(csv_file, delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL)
                psu_channel = power_supply.channels[psu_channel_no]
                ctx = Context(stdscr, csv_writer, strip, psu_channel, num_leds, settle_time, current_offset)
                run_measurements(ctx, mode, brightness_range, value_range, voltage_range)
        finally:
            strip.clear_strip()
            strip.cleanup()


def main():
    arg_parser = argparse.ArgumentParser()
    arg_parser.add_argument("output_file", metavar='output-file', help="CSV output file")
    arg_parser.add_argument("--mode", help="'individual' to enable each color individually, 'white' to enable all colors at once, or 'full' to cycle through all colors of the rainbow", default='individual')
    arg_parser.add_argument("--psu-port", dest='psu_port',    help="power supply port", default='/dev/ttyS0')
    arg_parser.add_argument("--psu-channel", dest='psu_channel',    help="power supply channel", default='0')
    arg_parser.add_argument("--strip-mosi", dest='strip_mosi', help="mosi pin number", default=10)
    arg_parser.add_argument("--strip-sclk", dest='strip_sclk', help="sclk pin number", default=11)
    arg_parser.add_argument("--strip-rgb-order", dest='strip_rgb_order', help="strip rgb data order", default='rgb')
    arg_parser.add_argument("--num-leds", dest='num_leds', help="number of LEDs in strip", default=20)
    arg_parser.add_argument("--min-brightness", dest='min_brightness', help="min global brightness to set", default=0)
    arg_parser.add_argument("--max-brightness", dest='max_brightness', help="max global brightness to set", default=31)
    arg_parser.add_argument("--brightness-step", dest='brightness_step', help="global brightness step size", default=1)
    arg_parser.add_argument("--min-value", dest='min_value', help="min LED value to set", default=0)
    arg_parser.add_argument("--max-value", dest='max_value', help="max LED value to set", default=255)
    arg_parser.add_argument("--value-step", dest='value_step', help="LED value step size", default=1)
    arg_parser.add_argument("--min-voltage", dest='min_voltage', help="min voltage to set", default=Default(5))
    arg_parser.add_argument("--max-voltage", dest='max_voltage', help="max voltage to set", default=Default(5))
    arg_parser.add_argument("--voltage-step", dest='voltage_step', help="voltage step size", default=0.25)
    arg_parser.add_argument("--settle-time", dest='settle_time_ms', help="number of milliseconds to wait between each measurement for the current to settle", default=100)
    arg_parser.add_argument("--current-offset", dest='current_offset', help="number of mA to deduct from the current measurements", default=0)
    arg_parser.add_argument("--force", action='store_true', help="don't abort if output-file already exists")
    args = arg_parser.parse_args()

    curses.wrapper(lambda stdscr: run(args, stdscr))

if __name__ == "__main__":
    main()
    
