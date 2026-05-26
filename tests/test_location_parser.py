"""Tests for oden.location_parser."""

import unittest

from oden.location_parser import (
    extract_coordinates,
    extract_coordinates_from_mgrs_stalle,
    extract_coordinates_from_url,
    extract_location,
    normalize_message_text,
)

# Expected WGS84 for 33VWE 64874 95103 (Norrköping area) — verified via mgrs library.
_MGRS_NORRKOPING = ("58.591473", "16.116022")


class TestNormalizeMessageText(unittest.TestCase):
    def test_strips_invisible_unicode(self):
        msg = "St\u2068\u2069älle: 33VWE 64874 95103"
        self.assertEqual(normalize_message_text(msg), "Ställe: 33VWE 64874 95103")


class TestExtractCoordinatesFromUrl(unittest.TestCase):
    def test_google_maps_percent_2c_uppercase(self):
        msg = "Check this https://maps.google.com/maps?q=59.514828%2C17.767852"
        result = extract_coordinates_from_url(msg)
        self.assertEqual(result, ("59.514828", "17.767852"))

    def test_no_location_url(self):
        msg = "Just a normal message with no maps link"
        self.assertIsNone(extract_coordinates_from_url(msg))


class TestExtractCoordinatesFromMgrsStalle(unittest.TestCase):
    def test_7s_report_stalle_with_address(self):
        msg = """7S RAPPORT
Till: 1:A PLUT
Från: EA
TNR: 230204
Stund: 230159
Ställe: 33VWE 64874 95103, Fiskebyvägen, Norrköping
Styrka: 1
Slag: Plastflaska
Sagesman: GUSTAV"""
        result = extract_coordinates_from_mgrs_stalle(msg)
        self.assertEqual(result, _MGRS_NORRKOPING)

    def test_stalle_without_mgrs(self):
        msg = "Ställe: Fiskebyvägen, Norrköping"
        self.assertIsNone(extract_coordinates_from_mgrs_stalle(msg))

    def test_stalle_with_invisible_chars(self):
        msg = "St\u2068\u2069älle: 33VWE 64874 95103, Fiskebyvägen"
        result = extract_coordinates_from_mgrs_stalle(msg)
        self.assertEqual(result, _MGRS_NORRKOPING)


class TestExtractLocation(unittest.TestCase):
    """Tests for the full extract_location() pipeline."""

    def test_mgrs_from_stalle_field(self):
        msg = "Ställe: 33VWE 64874 95103, Fiskebyvägen, Norrköping"
        result = extract_location(msg)
        self.assertEqual(result, _MGRS_NORRKOPING)

    def test_url_takes_priority_over_mgrs(self):
        msg = "Ställe: 33VWE 64874 95103\nhttps://maps.google.com/maps?q=59.514828%2C17.767852"
        result = extract_location(msg)
        self.assertEqual(result, ("59.514828", "17.767852"))

    def test_google_maps_url(self):
        msg = "https://maps.google.com/maps?q=59.514828%2C17.767852"
        self.assertEqual(extract_location(msg), ("59.514828", "17.767852"))

    def test_empty_message(self):
        self.assertIsNone(extract_location(""))
        self.assertIsNone(extract_location(None))

    def test_extract_coordinates_alias(self):
        msg = "Ställe: 33VWE 64874 95103"
        self.assertEqual(extract_coordinates(msg), _MGRS_NORRKOPING)


if __name__ == "__main__":
    unittest.main()
