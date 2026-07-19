from pathlib import Path
import re
import unittest


REPO = Path(__file__).resolve().parents[1]


def _function_body(source: str, signature: str) -> str:
    start = source.index(signature)
    brace_start = source.index("{", start)
    depth = 0
    for index in range(brace_start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace_start : index + 1]
    raise AssertionError(f"Could not find body for {signature}")


class DashboardJsonLifetimeContractTest(unittest.TestCase):
    def test_stack_local_scriptsig_is_copied_into_json_document(self) -> None:
        source = (REPO / "main/http_server/v2/handler_v2_dashboard.cpp").read_text()
        body = _function_body(source, "esp_err_t GET_V2_dashboard")

        self.assertRegex(
            body,
            re.compile(
                r'bh\["scriptSig"\]\s*=\s*JsonString\(cb\.scriptsig,\s*false\)\s*;'
            ),
        )
        self.assertNotRegex(
            body,
            re.compile(r'bh\["scriptSig"\]\s*=\s*cb\.scriptsig\s*;'),
        )


if __name__ == "__main__":
    unittest.main()
