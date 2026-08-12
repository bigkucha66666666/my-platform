from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from zipfile import ZIP_DEFLATED, ZipFile


HEADERS = ['participant_label', 'participant_url', 'room_name']


def xml_escape(value) -> str:
    text = '' if value is None else str(value)
    return (
        text.replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('"', '&quot;')
        .replace("'", '&apos;')
    )


def column_letter(index: int) -> str:
    letters = ''
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def inline_string_cell(ref: str, value, *, style_id: int | None = None) -> str:
    style_attr = f' s="{style_id}"' if style_id is not None else ''
    return (
        f'<c r="{ref}" t="inlineStr"{style_attr}>'
        f'<is><t>{xml_escape(value)}</t></is>'
        '</c>'
    )


def build_participant_link_rows(room_config: dict, *, labels: list[str], base_url: str, hash_func=None):
    room_name = room_config['name']
    secure_urls = bool(room_config.get('use_secure_urls'))
    normalized_base_url = base_url.rstrip('/')
    rows = []

    if secure_urls and hash_func is None:
        from otree.common import make_hash

        hash_func = make_hash

    for label in labels:
        params = {'participant_label': label}
        if secure_urls:
            params['hash'] = hash_func(label)
        rows.append(
            dict(
                participant_label=label,
                participant_url=f'{normalized_base_url}/room/{room_name}?{urlencode(params)}',
                room_name=room_name,
            )
        )
    return rows


def find_room_config(room_name: str, rooms=None) -> dict:
    if rooms is None:
        from settings import ROOMS

        rooms = ROOMS

    for room_config in rooms:
        if room_config.get('name') == room_name:
            return room_config
    raise ValueError(f'未找到 room：{room_name}')


def read_participant_labels(room_config: dict, *, settings_dir: Path | None = None) -> list[str]:
    label_file = room_config.get('participant_label_file')
    if not label_file:
        raise ValueError(f"room '{room_config.get('name', '')}' 未配置 participant_label_file，无法导出标签链接。")

    base_dir = settings_dir or Path(__file__).resolve().parent
    label_path = Path(label_file)
    if not label_path.is_absolute():
        label_path = base_dir / label_path

    labels = label_path.read_text(encoding='utf-8').split()
    unique_labels = list(dict.fromkeys(labels))
    if not unique_labels:
        raise ValueError(f'标签文件为空：{label_path}')
    return unique_labels


def xlsx_content_types_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>"""


def package_rels_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>"""


def workbook_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
  xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="participant_links" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>"""


def workbook_rels_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""


def styles_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="2">
    <font><sz val="11"/><color theme="1"/><name val="Calibri"/><family val="2"/></font>
    <font><u/><sz val="11"/><color rgb="FF0563C1"/><name val="Calibri"/><family val="2"/></font>
  </fonts>
  <fills count="2">
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="gray125"/></fill>
  </fills>
  <borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="2">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>
  </cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""


def core_props_xml(created_at: datetime) -> str:
    timestamp = created_at.replace(microsecond=0).isoformat() + 'Z'
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
  xmlns:dc="http://purl.org/dc/elements/1.1/"
  xmlns:dcterms="http://purl.org/dc/terms/"
  xmlns:dcmitype="http://purl.org/dc/dcmitype/"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>oTree participant links</dc:title>
  <dc:creator>participant_link_export</dc:creator>
  <cp:lastModifiedBy>participant_link_export</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">{timestamp}</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">{timestamp}</dcterms:modified>
</cp:coreProperties>"""


def app_props_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
  xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>oTree participant_link_export</Application>
</Properties>"""


def worksheet_xml(rows: list[dict]) -> tuple[str, str]:
    sheet_rows = []
    header_cells = [
        inline_string_cell(f'{column_letter(index)}1', header)
        for index, header in enumerate(HEADERS, start=1)
    ]
    sheet_rows.append(f'<row r="1">{"".join(header_cells)}</row>')

    hyperlink_tags = []
    relationship_tags = []
    for row_index, row in enumerate(rows, start=2):
        cells = []
        for column_index, header in enumerate(HEADERS, start=1):
            ref = f'{column_letter(column_index)}{row_index}'
            style_id = 1 if header == 'participant_url' else None
            cells.append(inline_string_cell(ref, row.get(header, ''), style_id=style_id))
        sheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')

        relationship_id = f'rId{row_index - 1}'
        hyperlink_tags.append(f'<hyperlink ref="B{row_index}" r:id="{relationship_id}"/>')
        relationship_tags.append(
            '<Relationship '
            f'Id="{relationship_id}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
            f'Target="{xml_escape(row.get("participant_url", ""))}" '
            'TargetMode="External"/>'
        )

    hyperlinks_xml = f'<hyperlinks>{"".join(hyperlink_tags)}</hyperlinks>' if hyperlink_tags else ''
    dimension = f'A1:C{max(len(rows) + 1, 1)}'
    sheet_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
  xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <dimension ref="{dimension}"/>
  <cols>
    <col min="1" max="1" width="22" customWidth="1"/>
    <col min="2" max="2" width="92" customWidth="1"/>
    <col min="3" max="3" width="18" customWidth="1"/>
  </cols>
  <sheetData>{"".join(sheet_rows)}</sheetData>
  {hyperlinks_xml}
</worksheet>"""
    rels_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  {''.join(relationship_tags)}
</Relationships>"""
    return sheet_xml, rels_xml


def write_participant_links_xlsx(rows: list[dict], output_path: Path | str):
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.utcnow()
    sheet_xml, sheet_rels_xml = worksheet_xml(rows)

    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', xlsx_content_types_xml())
        archive.writestr('_rels/.rels', package_rels_xml())
        archive.writestr('docProps/core.xml', core_props_xml(now))
        archive.writestr('docProps/app.xml', app_props_xml())
        archive.writestr('xl/workbook.xml', workbook_xml())
        archive.writestr('xl/_rels/workbook.xml.rels', workbook_rels_xml())
        archive.writestr('xl/styles.xml', styles_xml())
        archive.writestr('xl/worksheets/sheet1.xml', sheet_xml)
        archive.writestr('xl/worksheets/_rels/sheet1.xml.rels', sheet_rels_xml)

    return output


def default_output_path(room_name: str) -> Path:
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    return Path('outputs') / 'participant_links' / f'{room_name}_participant_links_{timestamp}.xlsx'


def export_room_links(*, room_name: str, base_url: str, output_path: Path | str | None = None):
    room_config = find_room_config(room_name)
    labels = read_participant_labels(room_config)
    rows = build_participant_link_rows(room_config, labels=labels, base_url=base_url)
    output = Path(output_path) if output_path else default_output_path(room_name)
    return write_participant_links_xlsx(rows, output), len(rows)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description='导出 oTree room 参与者专属链接为 Excel 文件。')
    parser.add_argument('--room', default='prod_room', help='settings.ROOMS 中的 room 名称，默认 prod_room。')
    parser.add_argument(
        '--base-url',
        default='http://127.0.0.1:8001',
        help='参与者访问实验的服务器地址，例如 https://example.com。',
    )
    parser.add_argument('--output', help='输出 .xlsx 路径；不填则写入 outputs/participant_links/。')
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    output, row_count = export_room_links(
        room_name=args.room,
        base_url=args.base_url,
        output_path=args.output,
    )
    print(f'已导出 {row_count} 条参与者链接：{output.resolve()}')


if __name__ == '__main__':
    main()
