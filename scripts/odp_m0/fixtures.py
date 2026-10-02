"""Synthetic ODP fixtures for the categories the real corpus does not contain.

The eight real files have no hidden slide, no speaker notes text and no formula
object, so those rules can only be checked against files we build ourselves. A
fixture proves that the protocol behaves; it is never a real quality result.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

MANIFEST = """<?xml version="1.0" encoding="UTF-8"?>
<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2">
 <manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.presentation"/>
 <manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>
 <manifest:file-entry manifest:full-path="styles.xml" manifest:media-type="text/xml"/>
{extra}</manifest:manifest>
"""

STYLES = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-styles xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0"
 xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" office:version="1.2">
 <office:automatic-styles>
  <style:page-layout style:name="PM1">
   <style:page-layout-properties fo:page-width="28cm" fo:page-height="21cm"/>
  </style:page-layout>
 </office:automatic-styles>
 <office:master-styles>
  <style:master-page style:name="Default" style:page-layout-name="PM1"/>
 </office:master-styles>
</office:document-styles>
"""

CONTENT_HEADER = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
 xmlns:presentation="urn:oasis:names:tc:opendocument:xmlns:presentation:1.0"
 xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
 xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"
 xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0"
 xmlns:xlink="http://www.w3.org/1999/xlink" office:version="1.2">
 <office:automatic-styles>
  <style:style style:name="dp-visible" style:family="drawing-page">
   <style:drawing-page-properties presentation:visibility="visible"/>
  </style:style>
  <style:style style:name="dp-hidden-parent" style:family="drawing-page">
   <style:drawing-page-properties presentation:visibility="hidden"/>
  </style:style>
  <style:style style:name="dp-hidden-child" style:family="drawing-page" style:parent-style-name="dp-hidden-parent"/>
 </office:automatic-styles>
 <office:body><office:presentation>
"""

CONTENT_FOOTER = """ </office:presentation></office:body></office:document-content>
"""

MATHML = """<?xml version="1.0" encoding="UTF-8"?>
<math:math xmlns:math="http://www.w3.org/1998/Math/MathML" display="block">
 <math:semantics><math:mrow><math:mi>d</math:mi><math:mo>=</math:mo>
 <math:msqrt><math:mrow><math:mi>x</math:mi><math:mo>+</math:mo><math:mi>y</math:mi></math:mrow></math:msqrt>
 </math:mrow></math:semantics></math:math>
"""


def _slide(name: str, style: str, *, title: str, body_xml: str = "", notes: str = "") -> str:
    notes_xml = ""
    if notes:
        notes_xml = (
            f'  <presentation:notes draw:style-name="dp-visible">'
            f'<draw:frame presentation:class="notes" svg:x="2cm" svg:y="2cm" svg:width="10cm" svg:height="5cm">'
            f"<draw:text-box><text:p>{notes}</text:p></draw:text-box></draw:frame></presentation:notes>\n"
        )
    return (
        f' <draw:page draw:name="{name}" draw:style-name="{style}" draw:master-page-name="Default">\n'
        f'  <draw:frame presentation:class="title" svg:x="1cm" svg:y="1cm" svg:width="20cm" svg:height="3cm">'
        f"<draw:text-box><text:p>{title}</text:p></draw:text-box></draw:frame>\n"
        f"{body_xml}{notes_xml}"
        f" </draw:page>\n"
    )


# A code paragraph with an explicit tab, a run of three spaces and a line break.
CODE_BODY = (
    '  <draw:frame draw:name="code" svg:x="1cm" svg:y="5cm" svg:width="20cm" svg:height="8cm">'
    "<draw:text-box>"
    "<text:p>SELECT<text:tab/>nom,<text:s text:c=\"3\"/>prix</text:p>"
    "<text:p>FROM<text:tab/>produit<text:line-break/>WHERE prix &gt; 10;</text:p>"
    "<text:p>repeated paragraph text</text:p>"
    "</draw:text-box></draw:frame>\n"
)
DUPLICATE_BODY = (
    '  <draw:frame draw:name="dup1" svg:x="1cm" svg:y="5cm" svg:width="20cm" svg:height="4cm">'
    "<draw:text-box><text:p>repeated paragraph text</text:p></draw:text-box></draw:frame>\n"
    '  <draw:frame draw:name="dup2" svg:x="1cm" svg:y="10cm" svg:width="20cm" svg:height="4cm">'
    "<draw:text-box><text:p>repeated paragraph text</text:p></draw:text-box></draw:frame>\n"
)
FORMULA_BODY = (
    '  <draw:frame draw:name="formula" svg:x="1cm" svg:y="14cm" svg:width="8cm" svg:height="3cm">'
    '<draw:object xlink:href="./Object 1"/></draw:frame>\n'
)


def write_hidden_notes_formula(target: Path) -> Path:
    """Slide 1 visible with code, slide 2 hidden by an inherited style, slide 3 visible.

    Slide 1 carries notes text and a formula object; slides 1 and 3 share a title.
    """
    content = (
        CONTENT_HEADER
        + _slide("page1", "dp-visible", title="Same title", body_xml=CODE_BODY + FORMULA_BODY,
                 notes="Notes secret: this line must never reach a student.")
        + _slide("page2", "dp-hidden-child", title="Hidden slide",
                 body_xml='  <draw:frame draw:name="h" svg:x="1cm" svg:y="5cm" svg:width="20cm" svg:height="6cm">'
                          "<draw:text-box><text:p>hidden slide body text</text:p>"
                          "</draw:text-box></draw:frame>\n")
        + _slide("page3", "dp-visible", title="Same title", body_xml=DUPLICATE_BODY)
        + CONTENT_FOOTER
    )
    extra = (
        ' <manifest:file-entry manifest:full-path="Object 1/" '
        'manifest:media-type="application/vnd.oasis.opendocument.formula"/>\n'
        ' <manifest:file-entry manifest:full-path="Object 1/content.xml" manifest:media-type="text/xml"/>\n'
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/vnd.oasis.opendocument.presentation")
        archive.writestr("META-INF/manifest.xml", MANIFEST.format(extra=extra))
        archive.writestr("content.xml", content)
        archive.writestr("styles.xml", STYLES)
        archive.writestr("Object 1/content.xml", MATHML)
    return target


def write_minimal(target: Path, *, slides: int = 1) -> Path:
    content = CONTENT_HEADER + "".join(
        _slide(f"page{i}", "dp-visible", title=f"Slide {i}") for i in range(1, slides + 1)
    ) + CONTENT_FOOTER
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/vnd.oasis.opendocument.presentation")
        archive.writestr("META-INF/manifest.xml", MANIFEST.format(extra=""))
        archive.writestr("content.xml", content)
        archive.writestr("styles.xml", STYLES)
    return target


def write_external_reference(target: Path) -> Path:
    body = (
        '  <draw:frame draw:name="remote" svg:x="1cm" svg:y="5cm" svg:width="8cm" svg:height="5cm">'
        '<draw:image xlink:href="https://example.invalid/remote.png"/></draw:frame>\n'
    )
    content = CONTENT_HEADER + _slide("page1", "dp-visible", title="External", body_xml=body) + CONTENT_FOOTER
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/vnd.oasis.opendocument.presentation")
        archive.writestr("META-INF/manifest.xml", MANIFEST.format(extra=""))
        archive.writestr("content.xml", content)
        archive.writestr("styles.xml", STYLES)
    return target


def write_doctype_bomb(target: Path) -> Path:
    """A content.xml with a doctype declaration, which the parser must refuse."""
    content = (
        '<?xml version="1.0"?>\n<!DOCTYPE office:document-content [ <!ENTITY payload "boom"> ]>\n'
        + CONTENT_HEADER.split("?>", 1)[1]
        + _slide("page1", "dp-visible", title="Doctype")
        + CONTENT_FOOTER
    )
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/vnd.oasis.opendocument.presentation")
        archive.writestr("META-INF/manifest.xml", MANIFEST.format(extra=""))
        archive.writestr("content.xml", content)
        archive.writestr("styles.xml", STYLES)
    return target


def write_path_escape(target: Path) -> Path:
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/vnd.oasis.opendocument.presentation")
        archive.writestr("META-INF/manifest.xml", MANIFEST.format(extra=""))
        archive.writestr("content.xml", CONTENT_HEADER + _slide("page1", "dp-visible", title="Escape") + CONTENT_FOOTER)
        archive.writestr("styles.xml", STYLES)
        archive.writestr("../escape.txt", "must not be read")
    return target


ALL_FIXTURES = {
    "hidden_notes_formula.odp": ("write_hidden_notes_formula",
                                  "visible/hidden slides, notes text, duplicate titles, duplicate paragraph, "
                                  "explicit tab and three-space runs, a MathML formula object"),
    "minimal.odp": ("write_minimal", "smallest valid presentation, used for run and timeout behaviour"),
    "external_reference.odp": ("write_external_reference", "an image referenced over https, which must be reported"),
    "doctype.odp": ("write_doctype_bomb", "content.xml with a doctype declaration, which must be refused"),
    "path_escape.odp": ("write_path_escape", "a ZIP entry outside the archive root, which must be refused"),
}


def write_all(target_dir: Path) -> dict:
    target_dir.mkdir(parents=True, exist_ok=True)
    written = {}
    for name, (function, description) in ALL_FIXTURES.items():
        globals()[function](target_dir / name)
        written[name] = description
    return written
