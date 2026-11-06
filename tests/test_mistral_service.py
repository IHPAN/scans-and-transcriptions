import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.mistral_service import ocr_image


class MistralServiceTests(unittest.TestCase):
    def test_sdk_response_and_table_content(self):
        with TemporaryDirectory() as folder:
            image = Path(folder) / "scan.png"
            image.write_bytes(b"image")
            with patch("mistralai.client.Mistral") as sdk:
                client = sdk.return_value.__enter__.return_value
                client.ocr.process.return_value.model_dump.return_value = {
                    "pages": [{"markdown": "Text\n[tbl-0.html](tbl-0.html)",
                               "tables": [{"id": "tbl-0.html", "content": "<table><tr><td>A</td></tr></table>"}]}],
                    "usage_info": {"pages_processed": 1},
                }
                model, response = ocr_image(str(image), api_key="test", timeout_seconds=30,
                                            table_format="html", include_blocks=True)
                sdk.assert_called_once_with(api_key="test", timeout_ms=30000)
                args = client.ocr.process.call_args.kwargs
                self.assertEqual(args["model"], model)
                self.assertEqual(args["document"]["image_url"], "data:image/png;base64,aW1hZ2U=")
                self.assertTrue(args["include_blocks"])
                self.assertTrue(args["include_image_base64"])
                self.assertEqual(args["table_format"], "html")
                self.assertIn("<table><tr><td>A</td></tr></table>", response.text)
                self.assertEqual(response.usage_metadata.pages_processed, 1)

    def test_sdk_error_is_reported(self):
        with TemporaryDirectory() as folder:
            image = Path(folder) / "scan.jpg"
            image.write_bytes(b"image")
            with patch("mistralai.client.Mistral") as sdk:
                sdk.return_value.__enter__.return_value.ocr.process.side_effect = RuntimeError("HTTP 502")
                with self.assertRaisesRegex(RuntimeError, "HTTP 502"):
                    ocr_image(str(image), api_key="test")


if __name__ == "__main__":
    unittest.main()
