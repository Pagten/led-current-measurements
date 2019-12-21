#!/usr/bin/python3
import sys
import csv
import argparse
import time
import datetime
import curses
from lib.koradserial import KoradSerial
from apa102_pi.driver import apa102

COLOR_BITSHIFTS = [16, 8, 0] # RGB

def get_red(rgb_color):
    return int((rgb_color & 0xFF0000) >> 16)
  
def get_green(rgb_color):
    return int((rgb_color & 0x00FF00) >> 8)
    
def get_blue(rgb_color):
    return int(rgb_color & 0x0000FF)

def set_strip_color(strip, num_leds, rgb_color, global_brightness):
    # Set color of each LED
    strip.global_brightness = global_brightness
    for led in range(0, num_leds):
        strip.set_pixel_rgb(led, rgb_color)
    
    # Program strip
    strip.show()
    
def run_measurements(stdscr, csv_writer, strip, psu_channel, num_leds, settle_time, brightness_range, value_range, current_offset):
    # Write header
    csv_writer.writerow(['Brightness (31)', 'Red (255)', 'Green (255)', 'Blue (255)', 'Voltage (V)', 'Current (mA)'])

    i = 0
    nb_iterations = len(brightness_range) * len(COLOR_BITSHIFTS) * len(value_range)
    start_time = datetime.datetime.now()
    for cur_brightness in brightness_range:
        for color_bitshift in COLOR_BITSHIFTS:
            for cur_color_value in value_range:
                # Calculate colors and RGB value
                rgb_color = cur_color_value << color_bitshift
                red = get_red(rgb_color)
                green = get_green(rgb_color)
                blue = get_blue(rgb_color)
                
                # Set strip color
                set_strip_color(strip, num_leds, rgb_color, cur_brightness)
                
                # Wait for current to stabilize
                time.sleep(settle_time)
                
                # Measure voltage and current
                voltage = psu_channel.output_voltage
                current = (psu_channel.output_current * 1000.0 / num_leds) - current_offset
                
                # Write measurement to file
                csv_writer.writerow([cur_brightness, red, green, blue, voltage, current])
                
                # Progress calculations
                i += 1
                progress = i / nb_iterations * 100
                time_elapsed = datetime.datetime.now() - start_time
                time_per_iteration = time_elapsed / i
                time_remaining = (nb_iterations - i) * time_per_iteration
                hours_remaining, remainder = divmod(time_remaining.total_seconds(), 3600)
                minutes_remaining, seconds_remaining = divmod(remainder, 60)

                # Print status
                stdscr.addstr(2, 0, f'Color: R{red:<3} G{green:<3} B{blue:<3}\t'
                                    f'Brightness: {cur_brightness:2}/31')
                stdscr.addstr(3, 0, f'Voltage: {voltage:4.2f} V\t\t'
                                    f'Current: {current:5.2f} mA')
                stdscr.addstr(4, 0, f'Progress: {progress:6.2f}%\t'
                                    f'Time remaining: {int(hours_remaining):2}:{int(minutes_remaining):02}:{int(seconds_remaining):02}')
                stdscr.refresh()
    # Clear LEDs
    strip.clear_strip()
    print()

                    
def main(stdscr):
    arg_parser = argparse.ArgumentParser()
    arg_parser.add_argument("output_file", metavar='output-file', help="CSV output file")
    arg_parser.add_argument("--psu-port", dest='psu_port',    help="power supply port", default='/dev/ttyS0')
    arg_parser.add_argument("--psu-channel", dest='psu_channel',    help="power supply channel", default='0')
    arg_parser.add_argument("--strip-mosi", dest='strip_mosi', help="mosi pin number", default=10)
    arg_parser.add_argument("--strip-sclk", dest='strip_sclk', help="sclk pin number", default=11)
    arg_parser.add_argument("--strip-rgb-order", dest='strip_rgb_order', help="strip rgb data order", default='rgb')
    arg_parser.add_argument("--num-leds", dest='num_leds', help="number of LEDs in strip", default=20)
    arg_parser.add_argument("--min-brightness", dest='min_brightness', help="min global brightness to set", default=0)
    arg_parser.add_argument("--max-brightness", dest='max_brightness', help="max global brightness to set", default=31)
    arg_parser.add_argument("--min-value", dest='min_value', help="min LED value to set", default=0)
    arg_parser.add_argument("--max-value", dest='max_value', help="max LED value to set", default=255)
    arg_parser.add_argument("--settle-time", dest='settle_time', help="number of milliseconds to wait between each measurement for the current to settle", default=100)
    arg_parser.add_argument("--current-offset", dest='current_offset', help="number of mA to deduct from the current measurements", default=0)
    args = arg_parser.parse_args()
    
    psu_port = args.psu_port
    psu_channel_no = int(args.psu_channel)
    mosi_pin = args.strip_mosi
    sclk_pin = args.strip_sclk
    rgb_order = args.strip_rgb_order
    current_offset = int(args.current_offset)
    
    num_leds = int(args.num_leds)
    brightness_range = range(int(args.min_brightness), int(args.max_brightness) + 1)
    value_range = range(int(args.min_value), int(args.max_value) + 1)
    settle_time = int(args.settle_time) / 1000.0
    
    with open(args.output_file, mode='w') as csv_file:
        strip = apa102.APA102(num_led=num_leds, mosi=mosi_pin, sclk=sclk_pin, order=rgb_order)
        with KoradSerial(psu_port) as power_supply:
            stdscr.addstr(0, 0, 'PSU model: {}'.format(power_supply.model))
            stdscr.refresh()

            csv_file.write('# Command:, {}\r\n'.format(" ".join(sys.argv[:])))
            csv_file.write('# PSU model:, {}\r\n'.format(power_supply.model))
            csv_file.write('\r\n')

            csv_writer = csv.writer(csv_file, delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL)
            psu_channel = power_supply.channels[psu_channel_no]
            run_measurements(stdscr, csv_writer, strip, psu_channel, num_leds, settle_time, brightness_range, value_range, current_offset)
        strip.cleanup()


if __name__ == "__main__":
    curses.wrapper(main)
    
