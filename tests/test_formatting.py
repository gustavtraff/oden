import datetime
import os
import tempfile
import unittest
from unittest.mock import patch

from oden.formatting import (
    _format_phone_number,
    _format_quote,
    create_fileid,
    create_message_filename,
    format_sender_display,
    get_message_filepath,
    get_safe_group_dir_path,
    get_unique_filename,
    update_location_frontmatter,
)
from oden.template_loader import render_report


class TestFormatting(unittest.TestCase):
    @patch("oden.config.VAULT_PATH", "mock_vault")
    def test_get_safe_group_dir_path(self):
        self.assertEqual(get_safe_group_dir_path("My Awesome Group"), os.path.join("mock_vault", "My Awesome Group"))
        self.assertEqual(
            get_safe_group_dir_path("Group/With/Slashes"), os.path.join("mock_vault", "Group_With_Slashes")
        )
        self.assertEqual(get_safe_group_dir_path("!@#$%^&*()"), os.path.join("mock_vault", "__________"))

    def test_format_phone_number(self):
        self.assertEqual(_format_phone_number("+1234567890"), " [[+1234567890]]")
        self.assertEqual(_format_phone_number("1234567890"), " [[1234567890]]")
        self.assertIsNone(_format_phone_number(None))

    def test_create_message_filename_classic(self):
        """Test classic filename format (DDHHMM-phone-name.md)"""
        dt = datetime.datetime(2025, 12, 18, 10, 30)
        self.assertEqual(create_message_filename(dt, "John Doe", "+123", "classic"), "181030-123-John_Doe.md")
        self.assertEqual(create_message_filename(dt, "Jane Doe", None, "classic"), "181030-Jane_Doe.md")
        self.assertEqual(create_message_filename(dt, None, "+123", "classic"), "181030-123.md")
        self.assertEqual(create_message_filename(dt, None, None, "classic"), "181030-unknown.md")
        self.assertEqual(create_message_filename(dt, "With/Slashes", "+123", "classic"), "181030-123-With_Slashes.md")

    def test_create_message_filename_tnr(self):
        """Test TNR-only filename format (DDHHMM.md)"""
        dt = datetime.datetime(2025, 12, 18, 10, 30)
        self.assertEqual(create_message_filename(dt, "John Doe", "+123", "tnr"), "181030.md")
        self.assertEqual(create_message_filename(dt, None, None, "tnr"), "181030.md")

    def test_create_message_filename_tnr_name(self):
        """Test TNR-name filename format (DDHHMM-name.md)"""
        dt = datetime.datetime(2025, 12, 18, 10, 30)
        self.assertEqual(create_message_filename(dt, "John Doe", "+123", "tnr-name"), "181030-John_Doe.md")
        self.assertEqual(create_message_filename(dt, "Jane Doe", None, "tnr-name"), "181030-Jane_Doe.md")
        # Falls back to just TNR when no name
        self.assertEqual(create_message_filename(dt, None, "+123", "tnr-name"), "181030.md")
        self.assertEqual(create_message_filename(dt, None, None, "tnr-name"), "181030.md")

    def test_create_fileid(self):
        """Test fileid generation (always TNR-phone-name format)"""
        dt = datetime.datetime(2025, 12, 18, 10, 30)
        self.assertEqual(create_fileid(dt, "John Doe", "+123"), "181030-123-John_Doe")
        self.assertEqual(create_fileid(dt, "Jane Doe", None), "181030-Jane_Doe")
        self.assertEqual(create_fileid(dt, None, "+123"), "181030-123")
        self.assertEqual(create_fileid(dt, None, None), "181030-unknown")
        self.assertEqual(create_fileid(dt, "With/Slashes", "+123"), "181030-123-With_Slashes")

    def test_get_unique_filename(self):
        """Test unique filename generation with suffixes"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # First file should use base name
            self.assertEqual(get_unique_filename(tmpdir, "181030.md"), "181030.md")

            # Create the file
            open(os.path.join(tmpdir, "181030.md"), "w").close()

            # Second file should get -1 suffix
            self.assertEqual(get_unique_filename(tmpdir, "181030.md"), "181030-1.md")

            # Create that file too
            open(os.path.join(tmpdir, "181030-1.md"), "w").close()

            # Third file should get -2 suffix
            self.assertEqual(get_unique_filename(tmpdir, "181030.md"), "181030-2.md")

    def test_get_unique_filename_with_name(self):
        """Test unique filename generation with name in filename"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # First file should use base name
            self.assertEqual(get_unique_filename(tmpdir, "181030-Nicklas.md"), "181030-Nicklas.md")

            # Create the file
            open(os.path.join(tmpdir, "181030-Nicklas.md"), "w").close()

            # Second file should get -1 suffix
            self.assertEqual(get_unique_filename(tmpdir, "181030-Nicklas.md"), "181030-Nicklas-1.md")

    def test_format_sender_display(self):
        self.assertEqual(format_sender_display("John Doe", "+123"), "John Doe ( [[+123]])")
        self.assertEqual(format_sender_display("Jane Doe", None), "Jane Doe")
        self.assertEqual(format_sender_display(None, "+123"), " [[+123]]")
        self.assertEqual(format_sender_display(None, None), "Okänd")

    @patch("oden.config.VAULT_PATH", "mock_vault")
    @patch("oden.config.FILENAME_FORMAT", "classic")
    @patch("oden.formatting.get_unique_filename", side_effect=lambda d, f: f)
    def test_get_message_filepath(self, mock_unique):
        dt = datetime.datetime(2025, 12, 18, 10, 30)
        # Test with safe group name (unique=False to skip unique filename logic)
        expected_path_safe = os.path.join("mock_vault", "My Group", "181030-123-John_Doe.md")
        self.assertEqual(get_message_filepath("My Group", dt, "John Doe", "+123", unique=False), expected_path_safe)

        # Test with unsafe group name
        expected_path_unsafe = os.path.join("mock_vault", "My_Group", "181030-123-John_Doe.md")
        self.assertEqual(get_message_filepath("My/Group", dt, "John Doe", "+123", unique=False), expected_path_unsafe)

    def test_format_quote(self):
        quote = {"authorName": "Jane Doe", "authorNumber": "+456", "text": "This is a quote."}
        formatted = _format_quote(quote)
        self.assertIn("> **Svarar på Jane Doe ( [[+456]]):**", formatted)
        self.assertIn("> This is a quote.", formatted)

    def test_format_quote_edge_cases(self):
        # Test with missing author name
        quote_no_name = {"authorNumber": "+456", "text": "A quote."}
        formatted_no_name = _format_quote(quote_no_name)
        self.assertIn("> **Svarar på  [[+456]]:**", formatted_no_name)

        # Test with missing author number
        quote_no_num = {"authorName": "Jane Doe", "text": "A quote."}
        formatted_no_num = _format_quote(quote_no_num)
        self.assertIn("> **Svarar på Jane Doe:**", formatted_no_num)

        # Test with missing text
        quote_no_text = {"authorName": "Jane Doe", "authorNumber": "+456"}
        formatted_no_text = _format_quote(quote_no_text)
        self.assertIn("> ...", formatted_no_text)

        # Test with multi-line text
        quote_multiline = {"authorName": "Jane Doe", "authorNumber": "+456", "text": "Line 1\nLine 2"}
        formatted_multiline = _format_quote(quote_multiline)
        self.assertIn("> Line 1", formatted_multiline)
        self.assertIn("> Line 2", formatted_multiline)


class TestLocationFrontmatter(unittest.TestCase):
    def test_update_location_adds_property_when_missing(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("---\nfileid: test-123\n---\n\n# Group\n")
            path = f.name

        try:
            self.assertTrue(update_location_frontmatter(path, "58.591473", "16.116022"))
            with open(path, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("location: [58.591473, 16.116022]", content)
            self.assertFalse(update_location_frontmatter(path, "58.591473", "16.116022"))
        finally:
            os.unlink(path)

    def test_update_location_replaces_existing(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("---\nfileid: test-123\nlocation: [59.0, 18.0]\n---\n\n# Group\n")
            path = f.name

        try:
            self.assertTrue(update_location_frontmatter(path, "59.514828", "17.767852"))
            with open(path, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("location: [59.514828, 17.767852]", content)
            self.assertNotIn("location: [59.0, 18.0]", content)
        finally:
            os.unlink(path)

    def test_update_location_no_op_without_frontmatter(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("# No frontmatter\n")
            path = f.name

        try:
            self.assertFalse(update_location_frontmatter(path, "58.591473", "16.116022"))
        finally:
            os.unlink(path)

    def test_render_report_includes_location_when_coords_present(self):
        content = render_report(
            fileid="161410-123-John",
            group_title="Test Group",
            group_id="group123",
            tnr="161410",
            timestamp_iso="2026-01-01T16:14:10+01:00",
            sender_display="John",
            lat="58.591473",
            lon="16.116022",
            message="Ställe: 33VWE 64874 95103",
        )
        self.assertIn("location: [58.591473, 16.116022]", content)
        self.assertIn("[Position](geo:58.591473,16.116022)", content)

    def test_render_report_omits_location_without_coords(self):
        content = render_report(
            fileid="161410-123-John",
            group_title="Test Group",
            group_id="group123",
            tnr="161410",
            timestamp_iso="2026-01-01T16:14:10+01:00",
            sender_display="John",
            message="Ställe: Fiskebyvägen, Norrköping",
        )
        self.assertNotIn("location:", content)
        self.assertNotIn("geo:", content)


if __name__ == "__main__":
    unittest.main()
