"""Regression tests for bounded window capture and subprocess fallback."""
import ctypes
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import perception


class CaptureRecoveryTests(unittest.TestCase):
    def test_capture_image_materializes_pixels_before_temp_file_is_removed(self):
        from Foundation import NSURL

        with tempfile.TemporaryDirectory() as td:
            source_png = Path(td) / "source.png"
            color_space = perception.Quartz.CGColorSpaceCreateDeviceRGB()
            ctx = perception.Quartz.CGBitmapContextCreate(
                None, 8, 8, 8, 32, color_space,
                perception.Quartz.kCGImageAlphaPremultipliedLast)
            perception.Quartz.CGContextSetRGBFillColor(ctx, 0.2, 0.7, 0.3, 1.0)
            perception.Quartz.CGContextFillRect(ctx, perception.Quartz.CGRectMake(0, 0, 8, 8))
            source_image = perception.Quartz.CGBitmapContextCreateImage(ctx)
            dest = perception.Quartz.CGImageDestinationCreateWithURL(
                NSURL.fileURLWithPath_(str(source_png)), "public.png", 1, None)
            perception.Quartz.CGImageDestinationAddImage(dest, source_image, None)
            self.assertTrue(perception.Quartz.CGImageDestinationFinalize(dest))

            def fake_capture(_wid, output):
                shutil.copyfile(source_png, output)
                return True

            with patch.object(perception, "capture_window", side_effect=fake_capture):
                image = perception.capture_image(123)

        self.assertIsNotNone(image)
        pixels = ctypes.create_string_buffer(8 * 8 * 4)
        bitmap = perception.Quartz.CGBitmapContextCreate(
            pixels, 8, 8, 8, 32, color_space,
            perception.Quartz.kCGImageAlphaPremultipliedLast)
        perception.Quartz.CGContextDrawImage(bitmap, perception.Quartz.CGRectMake(0, 0, 8, 8), image)
        self.assertGreater(sum(pixels.raw), 0)

    def test_capture_image_uses_subprocess_capture(self):
        image = object()
        def fake_capture(_wid, output):
            output.write_bytes(b"png")
            return True

        with patch.object(perception, "capture_window", side_effect=fake_capture) as subprocess_capture, \
             patch.object(perception, "_window_point_size", return_value=None), \
             patch.object(perception.Quartz, "CGImageSourceCreateWithData", return_value=object()), \
             patch.object(perception.Quartz, "CGImageSourceCreateImageAtIndex", return_value=image):
            result = perception.capture_image(123)

        self.assertIs(result, image)
        subprocess_capture.assert_called_once()

    def test_capture_image_resamples_retina_backing_to_point_size(self):
        with tempfile.TemporaryDirectory() as td:
            source_png = Path(td) / "retina.png"
            color_space = perception.Quartz.CGColorSpaceCreateDeviceRGB()
            ctx = perception.Quartz.CGBitmapContextCreate(
                None, 1600, 1200, 8, 1600 * 4, color_space,
                perception.Quartz.kCGImageAlphaPremultipliedLast)
            source_image = perception.Quartz.CGBitmapContextCreateImage(ctx)
            from Foundation import NSURL
            dest = perception.Quartz.CGImageDestinationCreateWithURL(
                NSURL.fileURLWithPath_(str(source_png)), "public.png", 1, None)
            perception.Quartz.CGImageDestinationAddImage(dest, source_image, None)
            self.assertTrue(perception.Quartz.CGImageDestinationFinalize(dest))

            def fake_capture(_wid, output):
                shutil.copyfile(source_png, output)
                return True

            # 1600x1200 capture of an 800x600-point window: a 2x Retina backing
            with patch.object(perception, "capture_window", side_effect=fake_capture), \
                 patch.object(perception, "_window_point_size", return_value=800):
                image = perception.capture_image(123)

        self.assertIsNotNone(image)
        self.assertEqual(perception.Quartz.CGImageGetWidth(image), 800)
        self.assertEqual(perception.Quartz.CGImageGetHeight(image), 600)

    def test_read_conversation_uses_subprocess_capture_in_production(self):
        win = perception.WindowInfo(wid=123, pid=1, title="微信",
                                    x=0, y=0, w=800, h=600)
        image = object()
        def fake_capture(_wid, output):
            output.write_bytes(b"png")
            return True

        with patch.object(perception, "find_wechat_window", return_value=win), \
             patch.object(perception, "capture_image") as memory_capture, \
             patch.object(perception, "capture_window", side_effect=fake_capture) as subprocess_capture, \
             patch("input_region.input_outline", return_value=(.32, .60, .65, .39)), \
             patch.object(perception.Quartz, "CGImageSourceCreateWithData", return_value=object()), \
             patch.object(perception.Quartz, "CGImageSourceCreateImageAtIndex", return_value=image), \
             patch.object(perception.Quartz, "CGImageGetWidth", return_value=800), \
             patch.object(perception.Quartz, "CGImageGetHeight", return_value=600), \
             patch.object(perception, "_fingerprint", return_value=b"new"), \
             patch.object(perception, "ocr_image", return_value=[]):
            result = perception.read_conversation()

        memory_capture.assert_not_called()
        subprocess_capture.assert_called_once()
        self.assertEqual(result["timing_ms"]["capture_path"], "subprocess")

    def test_screencapture_timeout_returns_failure(self):
        with tempfile.TemporaryDirectory() as td:
            fake = Path(td) / "screencapture"
            fake.write_text("#!/bin/sh\n/bin/sleep 1\n", encoding="utf-8")
            os.chmod(fake, 0o755)
            output = Path(td) / "chat.png"
            old_path = os.environ.get("PATH")
            os.environ["PATH"] = td
            try:
                started = time.monotonic()
                result = perception.capture_window(123, output, timeout_s=0.03)
                elapsed = time.monotonic() - started
            finally:
                if old_path is None:
                    os.environ.pop("PATH", None)
                else:
                    os.environ["PATH"] = old_path
            self.assertFalse(result)
            self.assertLess(elapsed, 0.25)

    def test_screencapture_missing_returns_failure(self):
        # An empty PATH means subprocess.run cannot even launch screencapture
        # (FileNotFoundError): that must degrade to a failed capture, not raise.
        with tempfile.TemporaryDirectory() as td:
            old_path = os.environ.get("PATH")
            os.environ["PATH"] = td
            try:
                result = perception.capture_window(123, Path(td) / "out.png")
            finally:
                if old_path is None:
                    os.environ.pop("PATH", None)
                else:
                    os.environ["PATH"] = old_path
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
