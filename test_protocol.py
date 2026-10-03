"""Regression coverage for console transcripts captured from real Ozone V3.50."""
import unittest
from ozone_client import OzoneError, parse_reply


class ConsoleReplies(unittest.TestCase):
    def test_expression_value(self):
        self.assertEqual(parse_reply('Elf.GetExprValue("sizeof(unsigned int)")',
            'Elf.GetExprValue ("sizeof(unsigned int)"); // returns 0x4\r\n'), 4)

    def test_read_returns_data_not_byte_count(self):
        self.assertEqual(parse_reply('Target.ReadU32(0xE000ED00)',
            'Target.ReadU32 (0xE000ED00); // returns 0x4, data is 0x410FC241\r\n'), 0x410FC241)

    def test_all_bits_set_is_valid_data(self):
        self.assertEqual(parse_reply('Target.ReadU32(0x20000000)',
            'Target.ReadU32 (0x20000000); // returns 0x4, data is 0xFFFFFFFF\r\n'), 0xFFFFFFFF)

    def test_unrelated_console_reply_does_not_satisfy_request(self):
        with self.assertRaises(OzoneError):
            parse_reply('Target.ReadU32(0x20000004)',
                'Target.ReadU32 (0x20000000); // returns 0x4, data is 0x1234\r\n')

    def test_short_and_failed_reads_are_rejected(self):
        for response in ('// returns 0x1, data is 0xAB', '// returns -1'):
            with self.subTest(response=response), self.assertRaises(OzoneError):
                parse_reply('Target.ReadU32(0x20000000)', 'Target.ReadU32 (0x20000000); ' + response)

    def test_plain_command_echo_is_not_result(self):
        with self.assertRaises(OzoneError):
            parse_reply('Target.ReadU32(0x20000000)', 'Target.ReadU32(0x20000000);\r\n')

    def test_console_write_failure_is_rejected(self):
        with self.assertRaises(OzoneError):
            parse_reply('Target.WriteU32(0x20000000, 0x1)',
                'Target.WriteU32 (0x20000000, 0x1); // returns -1\r\n')

    def test_successful_write_echo_from_real_ozone(self):
        reply = 'Target.WriteU32 (0x20005034, 0x43340000);\r\nOZP_end\r\n'
        self.assertEqual(parse_reply('Target.WriteU32(0x20005034, 0x43340000)', reply), 0)

    def test_unrelated_write_echo_cannot_confirm_operation(self):
        with self.assertRaises(OzoneError):
            parse_reply('Target.WriteU32(0x20005034, 0x43340000)',
                        'Target.WriteU32 (0x20005034, 0x0);\r\nOZP_end\r\n')

    def test_write_echo_followed_by_error_is_failure(self):
        with self.assertRaises(OzoneError):
            parse_reply('Target.WriteU8(0x20000000, 0x1)',
                        'Target.WriteU8 (0x20000000, 0x1);\r\nERROR(1): memory write failed\r\n')


if __name__ == '__main__':
    unittest.main()
