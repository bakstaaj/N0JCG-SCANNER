import unittest

from pi_p25_scanner.radioreference_import import (
    _v04d5_category_name,
    _v04d5_extract_talkgroups_from_value,
    _v04d5_frequency_candidates,
    _v04d5_location_category_ids,
    _v04d5_location_values,
    _v04d5_site_label,
)
from pi_p25_scanner.radioreference_picker_forced_v0_4d3m import (
    _extract_freqs,
    _site_county_directory,
    _site_id,
    _site_name,
)
from pi_p25_scanner.runtime_activity import RuntimeActivityTracker
from pi_p25_scanner.runtime_status import RuntimeStatusParser
from pi_p25_scanner.backend import ScannerManager, ScannerStatus


TENDERFOOT_II = {
    "siteId": 12917,
    "siteNumber": 17,
    "siteDescr": "Tenderfoot II",
    "siteCtid": 300,
    "rfss": 6,
    "siteFreqs": [
        {"lcn": 1, "freq": "851.6875", "use": None},
        {"lcn": 2, "freq": "852.225", "use": "d"},
        {"lcn": 3, "freq": "853.3", "use": "d"},
        {"lcn": 4, "freq": "853.5375", "use": "d"},
        {"lcn": 5, "freq": "853.75", "use": "d"},
        {"lcn": 6, "freq": "858.1875", "use": None},
        {"lcn": 7, "freq": "858.4375", "use": None},
        {"lcn": 8, "freq": "859.4375", "use": None},
    ],
}

EXPECTED_CONTROL_CHANNELS = [
    852_225_000,
    853_300_000,
    853_537_500,
    853_750_000,
]

WOLCOTT = {
    "siteId": 13351,
    "siteNumber": 17,
    "siteDescr": "Wolcott",
    "siteLocation": "Eagle, CO",
}


class RadioReferenceControlFrequencyTests(unittest.TestCase):
    def test_importer_uses_only_explicit_control_markers(self) -> None:
        frequencies, source, _ = _v04d5_frequency_candidates(
            {"sites": [TENDERFOOT_II]}, 12917
        )
        self.assertEqual(EXPECTED_CONTROL_CHANNELS, frequencies)
        self.assertEqual("selected-site-control-fields", source)
        self.assertNotIn(851_687_500, frequencies)

    def test_site_picker_uses_only_explicit_control_markers(self) -> None:
        frequencies = _extract_freqs(TENDERFOOT_II)
        self.assertEqual(EXPECTED_CONTROL_CHANNELS, frequencies)
        self.assertNotIn(851_687_500, frequencies)

    def test_site_picker_preserves_radio_reference_identity(self) -> None:
        self.assertEqual(12917, _site_id(TENDERFOOT_II))
        self.assertEqual("Tenderfoot II", _site_name(TENDERFOOT_II))

    def test_site_13351_is_wolcott_not_tenderfoot(self) -> None:
        self.assertEqual(13351, _site_id(WOLCOTT))
        self.assertEqual("Wolcott", _site_name(WOLCOTT))

    def test_importer_preserves_tenderfoot_site_description(self) -> None:
        label = _v04d5_site_label({"sites": [TENDERFOOT_II, WOLCOTT]}, 12917, "Cripple Creek")
        self.assertEqual("Tenderfoot II", label)

    def test_importer_does_not_fall_back_to_unmarked_frequencies(self) -> None:
        unmarked = {
            "siteId": 999,
            "siteFreqs": [
                {"freq": "851.6875", "use": None},
                {"freq": "858.1875", "use": None},
            ],
        }
        frequencies, source, _ = _v04d5_frequency_candidates(
            {"sites": [unmarked]}, 999
        )
        self.assertEqual([], frequencies)
        self.assertEqual("selected-site-no-explicit-control-markers", source)

    def test_multiple_counties_resolve_to_exact_rr_ids(self) -> None:
        class Service:
            def getCountryInfo(self, **_kwargs):
                return {"stateList": [{"stid": 8, "stateName": "Colorado", "stateCode": "CO"}]}

            def getStateInfo(self, **_kwargs):
                return {
                    "countyList": [
                        {"ctid": 262, "countyName": "Fremont"},
                        {"ctid": 287, "countyName": "Park"},
                        {"ctid": 300, "countyName": "Teller"},
                    ]
                }

        class Client:
            service = Service()

        state_id, selected, names_by_id, unmatched = _site_county_directory(
            Client(), {}, "CO", "Teller, Park County, Fremont"
        )
        self.assertEqual(8, state_id)
        self.assertEqual({"teller": 300, "park": 287, "fremont": 262}, selected)
        self.assertEqual("Park", names_by_id[287])
        self.assertEqual([], unmatched)


class RadioReferenceGeographicTalkgroupTests(unittest.TestCase):
    def test_comma_separated_locations_are_normalized_and_deduplicated(self) -> None:
        self.assertEqual(
            ["Teller", "El Paso", "Douglas"],
            _v04d5_location_values(" Teller, El Paso, teller, Douglas "),
        )
        self.assertEqual(
            ["Cripple Creek", "Woodland Park"],
            _v04d5_location_values(["Cripple Creek", "Woodland Park"]),
        )

    def test_radio_reference_tgcname_field_is_recognized(self) -> None:
        self.assertEqual("Teller County", _v04d5_category_name({"tgCid": 13365, "tgCname": "Teller County"}))

    def test_multiple_counties_and_cities_select_union_of_rr_categories(self) -> None:
        category_ids, missing_counties, missing_cities = _v04d5_location_category_ids(
            {
                13365: "Teller County",
                32655: "Cripple Creek",
                28528: "Woodland Park",
                99999: "El Paso County",
                12345: "Colorado State Patrol",
            },
            ["Teller", "El Paso County"],
            ["Cripple Creek", "Woodland Park"],
        )
        self.assertEqual([13365, 28528, 32655, 99999], category_ids)
        self.assertEqual([], missing_counties)
        self.assertEqual([], missing_cities)

    def test_selected_service_filter_keeps_local_police_dispatch(self) -> None:
        result = _v04d5_extract_talkgroups_from_value(
            {
                "talkgroups": [
                    {
                        "tgCid": 32655,
                        "tgDec": 6138,
                        "tgAlpha": "Cripple Creek PD",
                        "tgDescr": "Police Dispatch",
                        "enc": 0,
                        "tags": [{"tagId": 2, "tagDescr": None}],
                    },
                    {
                        "tgCid": 32655,
                        "tgDec": 6150,
                        "tgAlpha": "Cripple Creek PW",
                        "tgDescr": "Public Works",
                        "enc": 0,
                        "tags": [{"tagId": 14, "tagDescr": None}],
                    },
                ]
            },
            {32655: "Cripple Creek"},
            ["Law Enforcement"],
            False,
        )
        self.assertEqual([6138], [item["tgid"] for item in result])
        self.assertEqual("Law Enforcement", result[0]["category"])


class ControlChannelHuntStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = RuntimeStatusParser()

    def test_hunt_frequency_is_parsed_as_searching(self) -> None:
        update = self.parser.parse_line(
            "07/20/26 20:31:34 set control channel=853.537500"
        )
        self.assertEqual(853_537_500, update.control_frequency_hz)
        self.assertEqual("searching", update.control_channel_state)

    def test_timeout_is_searching(self) -> None:
        update = self.parser.parse_line("control channel timeout")
        self.assertEqual("searching", update.control_channel_state)

    def test_control_activity_is_locked(self) -> None:
        update = self.parser.parse_line("tsbk network status broadcast nac=0xd11")
        self.assertEqual("locked", update.control_channel_state)

    def test_op25_tgid_assignment_is_lock_and_voice_activity(self) -> None:
        update = self.parser.parse_line("08/10/26 new tgid=1107  prio 3")
        self.assertEqual(1107, update.tgid)
        self.assertTrue(update.voice_call)
        self.assertEqual("locked", update.control_channel_state)

    def test_op25_new_frequency_is_control_activity(self) -> None:
        update = self.parser.parse_line("08/10/26 new freq=852.225000")
        self.assertEqual(852225000, update.voice_frequency_hz)
        self.assertEqual("locked", update.control_channel_state)

    def test_nac_reconfiguration_is_locked(self) -> None:
        update = self.parser.parse_line(
            "Reconfiguring NAC from 0x000 to 0xd11"
        )
        self.assertEqual("locked", update.control_channel_state)

    def test_launch_command_enables_hunt_frequency_logging(self) -> None:
        manager = ScannerManager.__new__(ScannerManager)
        command = manager._with_browser_audio_udp(["rx.py", "-v", "1"])
        verbosity_index = command.index("-v") + 1
        self.assertEqual("5", command[verbosity_index])

    def test_unknown_tgid_does_not_reuse_previous_label(self) -> None:
        manager = ScannerManager.__new__(ScannerManager)
        manager.status = ScannerStatus()
        manager.activity_tracker = RuntimeActivityTracker()
        manager.talkgroup_labels = {6132: "Teller SO 1"}
        manager.blocked_talkgroup_ids = set()
        manager._display_suppressed_tgid_until = {}

        manager._apply_runtime_status_update(
            self.parser.parse_line("07/20/26 set tgid=6132, srcaddr=614523")
        )
        self.assertEqual(6132, manager.status.active_tgid)
        self.assertEqual("Teller SO 1", manager.status.active_talkgroup_label)

        manager._apply_runtime_status_update(
            self.parser.parse_line("07/20/26 set tgid=2522, srcaddr=614523")
        )
        self.assertEqual(2522, manager.status.active_tgid)
        self.assertEqual("", manager.status.active_talkgroup_label)
        self.assertEqual("", manager.status.last_active_talkgroup_label)


if __name__ == "__main__":
    unittest.main()
