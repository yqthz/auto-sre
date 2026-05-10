import json
import unittest

from app.notification.send_report import _render_report_markdown


class TestSendReportRendering(unittest.TestCase):
    def test_render_report_shows_direct_evidence_text_and_runbook_names(self):
        payload = {
            "summary": "service stopped after a container exit",
            "severity": "critical",
            "impact_scope": "newbee-mall backend service",
            "timeline": [
                {
                    "time": "2026-05-08T12:18:36Z",
                    "source": "log",
                    "event": "container exited with code 143",
                    "evidence": [
                        "2026-05-08 12:18:36 ERROR container exited with code 143",
                        "libgcc_s.so.1: No such file or directory",
                    ],
                }
            ],
            "root_causes": [
                {
                    "hypothesis": "missing shared library caused the JVM to fail",
                    "confidence": 0.92,
                    "evidence": [
                        "java.lang.UnsatisfiedLinkError: /opt/java/openjdk/lib/amd64/libfontmanager.so",
                    ],
                    "reasoning": "the error appears directly in the captured application log",
                }
            ],
            "recommendations": ["restore the missing shared library and restart the service"],
            "runbook_refs": ["newbee-mall-app-container-recovery"],
            "risk_notes": "restart may briefly interrupt traffic",
        }

        markdown = _render_report_markdown("Incident Diagnostic Report: InstanceDown", json.dumps(payload, ensure_ascii=False))

        self.assertIn("2026-05-08 12:18:36 ERROR container exited with code 143", markdown)
        self.assertIn("libgcc_s.so.1: No such file or directory", markdown)
        self.assertIn("java.lang.UnsatisfiedLinkError", markdown)
        self.assertIn("newbee-mall-app-container-recovery", markdown)
        self.assertIn("Reasoning:", markdown)


if __name__ == "__main__":
    unittest.main()
