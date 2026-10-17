import unittest
import tempfile
import datetime
from pathlib import Path
from unittest.mock import Mock, call, patch

from PIL import Image

from app import (
    ANIMATION_DESTINATIONS,
    KeyboardScreen,
    OP_DATA,
    OP_INFO,
    REPORT_ID_0_PIDS,
    SCREEN_SIZES,
    adjust_brightness,
    build_report,
    crc16_modbus,
    inner_packet,
    prepare_gif_frames,
    render_image,
    to_rgb565,
)


class ProtocolTests(unittest.TestCase):
    def test_b75_pro_max_display_settings(self):
        self.assertEqual(SCREEN_SIZES[12744], (160, 96))
        self.assertIn(12744, REPORT_ID_0_PIDS)

    def test_animation_destinations_have_distinct_frame_limits(self):
        self.assertEqual(ANIMATION_DESTINATIONS["GIF page"], (1, 160))
        self.assertEqual(ANIMATION_DESTINATIONS["Power-on animation"], (0, 64))

    def test_clear_animation_targets_selected_destination(self):
        screen = KeyboardScreen.__new__(KeyboardScreen)
        screen.send = Mock()
        screen.finish = Mock()
        screen.clear_animation(gif_type=0)
        self.assertEqual(
            screen.send.call_args_list,
            [
                call(OP_INFO, inner_packet(18, 2, b"\x00\x00")),
                call(OP_INFO, inner_packet(19, 2, b"\x00\x00")),
            ],
        )
        self.assertEqual(screen.finish.call_count, 2)

    def test_clear_animation_targets_gif_page_only_by_default(self):
        screen = KeyboardScreen.__new__(KeyboardScreen)
        screen.send = Mock()
        screen.finish = Mock()
        screen.clear_animation()
        self.assertEqual(
            screen.send.call_args_list,
            [
                call(OP_INFO, inner_packet(18, 2, b"\x01\x00")),
                call(OP_INFO, inner_packet(19, 2, b"\x01\x00")),
            ],
        )
        self.assertEqual(screen.finish.call_count, 2)

    def test_clear_startup_animation_targets_boot_slot_only(self):
        screen = KeyboardScreen.__new__(KeyboardScreen)
        screen.send = Mock()
        screen.finish = Mock()
        screen.clear_animation(gif_type=0)
        self.assertEqual(
            screen.send.call_args_list,
            [
                call(OP_INFO, inner_packet(18, 2, b"\x00\x00")),
                call(OP_INFO, inner_packet(19, 2, b"\x00\x00")),
            ],
        )
        self.assertEqual(screen.finish.call_count, 2)

    def test_upload_animation_uses_boot_animation_type(self):
        screen = KeyboardScreen.__new__(KeyboardScreen)
        screen.send = Mock()
        screen.finish = Mock()
        with patch("app.time.sleep"):
            screen.upload_animation([b"\0\0"], 0, 10, Mock())
        sent_packets = [call.args[1] for call in screen.send.call_args_list]
        self.assertIn(inner_packet(18, 2, b"\x00\x00"), sent_packets)
        self.assertIn(inner_packet(19, 2, b"\x00\x0a"), sent_packets)
        self.assertIn(inner_packet(16, 3, b"\x02\x00\x00"), sent_packets)

    def test_sync_time_sends_local_clock_and_date(self):
        screen = KeyboardScreen.__new__(KeyboardScreen)
        screen.send = Mock()
        screen.finish = Mock()
        local_time = datetime.datetime(2026, 10, 17, 13, 45, 6)
        screen.sync_time(now=local_time)
        expected = []
        time_bytes = bytes((13, 45, 6))
        date_bytes = bytes((26, 6, 10, 17))
        for _ in range(3):
            expected.extend(
                [
                    call(OP_INFO, inner_packet(9, 3)),
                    call(OP_DATA, time_bytes),
                    call(OP_INFO, inner_packet(10, 4)),
                    call(OP_DATA, date_bytes),
                ]
            )
        self.assertEqual(screen.send.call_args_list, expected)
        self.assertEqual(screen.finish.call_count, 6)

    def test_modbus_crc_known_vector(self):
        self.assertEqual(crc16_modbus(b"123456789"), bytes.fromhex("4b37"))

    def test_inner_packet_contains_big_endian_length_and_header_crc(self):
        header = bytes((11, 0, 0))
        self.assertEqual(inner_packet(11, 0), b"\xa5\x5a" + header + crc16_modbus(header))
        self.assertEqual(inner_packet(12, 0x1234, b"\x01")[:5], b"\xa5\x5a\x0c\x12\x34")

    def test_report_checksum_covers_report_with_checksum_zeroed(self):
        report = build_report(0x41, 0x0102, b"abc", 3)
        checksum = report[4] | (report[5] << 8)
        check = bytearray(report)
        check[4:6] = b"\0\0"
        self.assertEqual(len(report), 63)
        self.assertEqual(checksum, sum(check) & 0xFFFF)
        self.assertEqual(report[:3], bytes((0x41, 0x02, 0x01)))


class ImageTests(unittest.TestCase):
    def test_fit_letterboxes_and_fill_matches_requested_size(self):
        source = Image.new("RGB", (2, 1), (255, 0, 0))
        fitted = render_image(source, (4, 4), "fit")
        self.assertEqual(fitted.size, (4, 4))
        self.assertEqual(fitted.getpixel((0, 0)), (0, 0, 0))
        self.assertEqual(fitted.getpixel((1, 1)), (255, 0, 0))
        self.assertEqual(render_image(source, (4, 4), "fill").size, (4, 4))

    def test_transparency_composites_to_black(self):
        source = Image.new("RGBA", (1, 1), (255, 0, 0, 0))
        self.assertEqual(render_image(source, (1, 1), "fit").getpixel((0, 0)), (0, 0, 0))

    def test_rgb565_is_big_endian(self):
        pixels = to_rgb565(Image.new("RGB", (1, 1), (255, 0, 0)))
        self.assertEqual(pixels, b"\xf8\x00")

    def test_brightness_adjustment_changes_only_uploaded_still_image_pixels(self):
        source = Image.new("RGB", (1, 1), (100, 100, 100))
        self.assertEqual(adjust_brightness(source, 100).getpixel((0, 0)), (100, 100, 100))
        self.assertEqual(adjust_brightness(source, 0).getpixel((0, 0)), (0, 0, 0))
        self.assertEqual(adjust_brightness(source, 200).getpixel((0, 0)), (200, 200, 200))

    def test_brightness_adjustment_rejects_out_of_range_values(self):
        source = Image.new("RGB", (1, 1), (100, 100, 100))
        with self.assertRaisesRegex(ValueError, "between 0% and 200%"):
            adjust_brightness(source, 201)

    def test_gif_frame_limit_is_checked_before_preparing_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            gif_path = Path(directory) / "too-many-frames.gif"
            frames = [Image.new("RGB", (1, 1), (index, 0, 0)) for index in range(161)]
            frames[0].save(gif_path, save_all=True, append_images=frames[1:], duration=100)
            with Image.open(gif_path) as gif:
                with self.assertRaisesRegex(ValueError, "at most 160"):
                    prepare_gif_frames(gif, (1, 1), "fit")

    def test_boot_animation_frame_limit_is_checked_before_preparing_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            gif_path = Path(directory) / "too-many-boot-frames.gif"
            frames = [Image.new("RGB", (1, 1), (index, 0, 0)) for index in range(65)]
            frames[0].save(gif_path, save_all=True, append_images=frames[1:], duration=100)
            with Image.open(gif_path) as gif:
                with self.assertRaisesRegex(ValueError, "at most 64"):
                    prepare_gif_frames(gif, (1, 1), "fit", max_frames=64)


if __name__ == "__main__":
    unittest.main()
