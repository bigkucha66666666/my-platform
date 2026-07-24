import tempfile
import unittest
import zipfile
from pathlib import Path

import participant_link_export as exporter


class ParticipantLinkExportTests(unittest.TestCase):
    def test_builds_secure_room_links(self):
        room_config = dict(name='prod_room', use_secure_urls=True)
        rows = exporter.build_participant_link_rows(
            room_config,
            labels=['P001', 'P002'],
            base_url='https://example.test/',
            hash_func=lambda label: f'hash-{label}',
        )

        self.assertEqual(rows[0]['participant_label'], 'P001')
        self.assertEqual(rows[0]['room_name'], 'prod_room')
        self.assertEqual(
            rows[0]['participant_url'],
            'https://example.test/room/prod_room?participant_label=P001&hash=hash-P001',
        )
        self.assertEqual(
            rows[1]['participant_url'],
            'https://example.test/room/prod_room?participant_label=P002&hash=hash-P002',
        )

    def test_writes_xlsx_with_clickable_links(self):
        rows = [
            dict(
                participant_label='P001',
                participant_url='https://example.test/room/prod_room?participant_label=P001&hash=abc123',
                room_name='prod_room',
            )
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / 'participant_links.xlsx'
            exporter.write_participant_links_xlsx(rows, output_path)

            self.assertTrue(output_path.exists())
            with zipfile.ZipFile(output_path) as archive:
                names = set(archive.namelist())
                self.assertIn('xl/worksheets/sheet1.xml', names)
                self.assertIn('xl/worksheets/_rels/sheet1.xml.rels', names)
                sheet_xml = archive.read('xl/worksheets/sheet1.xml').decode('utf-8')
                rels_xml = archive.read('xl/worksheets/_rels/sheet1.xml.rels').decode('utf-8')

            self.assertIn('participant_label', sheet_xml)
            self.assertIn('P001', sheet_xml)
            self.assertIn('<hyperlink ref="B2" r:id="rId1"', sheet_xml)
            self.assertIn(
                'Target="https://example.test/room/prod_room?participant_label=P001&amp;hash=abc123"',
                rels_xml,
            )

    def test_single_bottleneck_admin_report_has_excel_button(self):
        template = Path('single_bottleneck/admin_report.html').read_text(encoding='utf-8')

        self.assertIn('export-participant-links-excel', template)
        self.assertIn('participant-link-data', template)
        self.assertIn('exportParticipantLinksExcel', template)

    def test_single_bottleneck_admin_report_links_3d_demo(self):
        template = Path('single_bottleneck/admin_report.html').read_text(encoding='utf-8')

        self.assertIn('单瓶颈 3D 动态演示', template)
        self.assertIn("{{ static 'single_bottleneck/Bottleneck3DDemo.html' }}", template)
        self.assertTrue(Path('_static/single_bottleneck/Bottleneck3DDemo.html').exists())

    def test_single_bottleneck_admin_report_links_unified_start_control(self):
        template = Path('single_bottleneck/admin_report.html').read_text(encoding='utf-8')

        self.assertIn('open-unified-start-control', template)
        self.assertIn('/SessionMonitor/', template)
        self.assertIn('推进最慢参与者', template)
        self.assertIn('可能需要多次操作', template)


if __name__ == '__main__':
    unittest.main()
