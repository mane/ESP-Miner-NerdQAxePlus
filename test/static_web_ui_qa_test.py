from pathlib import Path
import unittest


REPO = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (REPO / relative_path).read_text()


class WebUiQaContractTest(unittest.TestCase):
    def test_swarm_network_activity_is_cancelled_when_route_is_destroyed(self) -> None:
        source = _read("main/http_server/axe-os/src/app/pages/swarm/swarm.component.ts")

        self.assertIn("Subject", source)
        self.assertIn("takeUntil", source)
        self.assertIn("private destroy$ = new Subject<void>();", source)
        self.assertIn("this.destroy$.next();", source)
        self.assertIn("this.destroy$.complete();", source)
        self.assertGreaterEqual(source.count("takeUntil(this.destroy$)"), 4)

    def test_swarm_table_is_wrapped_in_horizontal_scroll_container(self) -> None:
        template = _read("main/http_server/axe-os/src/app/pages/swarm/swarm.component.html")
        styles = _read("main/http_server/axe-os/src/app/pages/swarm/swarm.component.scss")
        table_pos = template.index("<table class=\"swarm-table\">")
        wrapper_pos = template.index("<div class=\"table-container\">")
        close_pos = template.index("<!-- Legend -->")

        self.assertLess(wrapper_pos, table_pos)
        self.assertGreater(close_pos, table_pos)
        self.assertIn("</div>\n\n      <!-- Legend -->", template)
        self.assertIn("overflow-x: auto;", styles)
        self.assertIn("text-align: center;", styles)

    def test_loading_operator_tears_down_wrapped_source_on_unsubscribe(self) -> None:
        source = _read("main/http_server/axe-os/src/app/services/loading.service.ts")

        self.assertIn("const subscription = source.subscribe", source)
        self.assertIn("return () =>", source)
        self.assertIn("subscription.unsubscribe();", source)

    def test_fan_slider_full_speed_hint_is_not_s19_simulator_copy(self) -> None:
        template = _read("main/http_server/axe-os/src/app/pages/edit/edit.component.html")

        self.assertNotIn("S19 Simulator", template)
        self.assertIn("Full speed", template)


if __name__ == "__main__":
    unittest.main()
