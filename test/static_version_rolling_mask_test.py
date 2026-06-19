import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
ASIC_H = ROOT / "components/bm1397/include/asic.h"
ASIC_CPP = ROOT / "components/bm1397/asic.cpp"
CREATE_JOBS = ROOT / "main/tasks/create_jobs_task.cpp"
CAN_SENDER_H = ROOT / "main/tasks/can_sender.h"
CAN_SLAVE = ROOT / "main/tasks/can_slave_task.cpp"
BM_CHIP_FILES = [
    ROOT / "components/bm1397/bm1366.cpp",
    ROOT / "components/bm1397/bm1368.cpp",
    ROOT / "components/bm1397/bm1370.cpp",
    ROOT / "components/bm1397/bm1373.cpp",
]


class VersionRollingMaskContractTest(unittest.TestCase):
    def test_asic_exposes_mask_helper_that_converts_bip310_to_chip_mask(self):
        header = ASIC_H.read_text()
        source = ASIC_CPP.read_text()

        self.assertIn("ASIC_DEFAULT_VERSION_MASK", header)
        self.assertIn("void setVersionMask(uint32_t version_mask);", header)
        self.assertIn("void Asic::setVersionMask(uint32_t version_mask)", source)
        self.assertRegex(source, r"version_mask\s*>>\s*13")
        self.assertRegex(source, r"&\s*0xFFFF")
        self.assertRegex(source, r"send6\(CMD_WRITE_ALL,\s*0x00,\s*0xA4,\s*0x90,\s*0x00")

    def test_chip_initializers_do_not_hardcode_full_version_rolling_mask(self):
        hardcoded_full_mask = "send6(CMD_WRITE_ALL, 0x00, 0xA4, 0x90, 0x00, 0xFF, 0xFF)"
        for path in BM_CHIP_FILES:
            source = path.read_text()
            self.assertNotIn(hardcoded_full_mask, source, path)
            self.assertIn("setVersionMask(ASIC_DEFAULT_VERSION_MASK)", source, path)

    def test_job_task_updates_local_and_can_slave_masks_before_work_dispatch(self):
        source = CREATE_JOBS.read_text()
        self.assertIn("active_version_mask", source)
        self.assertRegex(source, r"asics->setVersionMask\(next_job->version_mask\)")
        self.assertLess(source.index("asics->setVersionMask(next_job->version_mask)"), source.index("asics->sendWork"))
        self.assertIn("CAN_CMD_SET_VERSION_MASK", source)
        self.assertIn("can_send_settings_cmd", source)

    def test_can_slaves_accept_version_mask_updates_without_changing_job_payload(self):
        sender_h = CAN_SENDER_H.read_text()
        slave = CAN_SLAVE.read_text()
        self.assertIn("CAN_CMD_SET_VERSION_MASK", sender_h)
        self.assertIn("CAN_CMD_SET_VERSION_MASK", slave)
        self.assertRegex(slave, r"memcpy\(&version_mask,\s*p \+ 1,\s*sizeof\(version_mask\)\)")
        self.assertRegex(slave, r"asics->setVersionMask\(version_mask\)")
        self.assertIn("#define JOB_PAYLOAD_LEN (sizeof(BM1368_job) + sizeof(uint32_t))", slave)


if __name__ == "__main__":
    unittest.main()
