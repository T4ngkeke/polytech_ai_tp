"""ODP files built for the M2-A tests.

The real courseware cannot cover every rule: the first decks have no formula
object, and a test must not depend on one teacher's file anyway. These fixtures
exercise the protocol. A fixture passing says the code behaves; it never says
anything about quality on real material.

Nothing here reads or writes the courseware in cours/.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

PRESENTATION_MIME = 'application/vnd.oasis.opendocument.presentation'

# A small but valid PNG: an 8x8 white square. Enough to be stored as an asset.
PNG_BYTES = bytes.fromhex(
    '89504e470d0a1a0a0000000d49484452000000080000000808020000004b6d29dc'
    '0000001649444154789c63fcffff3f032d01230c0c0c0c00b6ff03fd8b9e6b0000'
    '000049454e44ae426082'
)

MANIFEST = """<?xml version="1.0" encoding="UTF-8"?>
<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2">
 <manifest:file-entry manifest:full-path="/" manifest:media-type="{root_type}"/>
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

CONTENT_FOOTER = ' </office:presentation></office:body></office:document-content>\n'

# The formula object keeps its own StarMath annotation. It is the author's text,
# not a LaTeX transcription we produced.
FORMULA_XML = """<?xml version="1.0" encoding="UTF-8"?>
<math:math xmlns:math="http://www.w3.org/1998/Math/MathML" display="block">
 <math:semantics>
  <math:mrow><math:mi>d</math:mi><math:mo>=</math:mo>
   <math:msqrt><math:mrow><math:mi>x</math:mi><math:mo>+</math:mo><math:mi>y</math:mi></math:mrow></math:msqrt>
  </math:mrow>
  <math:annotation encoding="StarMath 5.0">d = sqrt{x + y}</math:annotation>
 </math:semantics>
</math:math>
"""

SPREADSHEET_XML = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" office:version="1.2">
 <office:body><office:spreadsheet>
  <table:table table:name="Feuille1">
   <table:table-row>
    <table:table-cell><text:p>id</text:p></table:table-cell>
    <table:table-cell table:number-columns-repeated="3"><text:p>col</text:p></table:table-cell>
    <table:table-cell table:number-columns-repeated="900"/>
   </table:table-row>
   <table:table-row>
    <table:table-cell><text:p>1</text:p></table:table-cell>
    <table:table-cell table:number-columns-spanned="2"><text:p>fusion</text:p></table:table-cell>
    <table:covered-table-cell/>
    <table:table-cell><text:p>fin</text:p></table:table-cell>
   </table:table-row>
   <table:table-row table:number-rows-repeated="500"><table:table-cell table:number-columns-repeated="900"/></table:table-row>
  </table:table>
 </office:spreadsheet></office:body></office:document-content>
"""

# An embedded drawing with two labels and a picture of its own. The picture must
# not disappear because it sits inside an object.
GRAPHICS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
 xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0"
 xmlns:xlink="http://www.w3.org/1999/xlink" office:version="1.2">
 <office:body><office:drawing>
  <draw:page draw:name="page1">
   <draw:custom-shape draw:name="n1"><text:p>Sommet A</text:p></draw:custom-shape>
   <draw:custom-shape draw:name="n2"><text:p>Sommet B</text:p></draw:custom-shape>
   <draw:connector draw:name="e1"/>
   <draw:frame draw:name="inner"><draw:image xlink:href="Pictures/inside-odg.png"/></draw:frame>
  </draw:page>
 </office:drawing></office:body></office:document-content>
"""


def _frame(name: str, *, x: float, y: float, body: str, width: float = 20.0, height: float = 3.0,
           presentation_class: str | None = None) -> str:
    klass = f' presentation:class="{presentation_class}"' if presentation_class else ''
    return (
        f'  <draw:frame draw:name="{name}"{klass} svg:x="{x}cm" svg:y="{y}cm" '
        f'svg:width="{width}cm" svg:height="{height}cm">{body}</draw:frame>\n'
    )


def _text_box(paragraphs: str) -> str:
    return f'<draw:text-box>{paragraphs}</draw:text-box>'


def _notes(text: str) -> str:
    return (
        '  <presentation:notes draw:style-name="dp-visible">'
        '<draw:frame presentation:class="notes" svg:x="2cm" svg:y="2cm" svg:width="10cm" svg:height="5cm">'
        f'<draw:text-box><text:p>{text}</text:p></draw:text-box></draw:frame></presentation:notes>\n'
    )


def _page(name: str, style: str, body: str) -> str:
    return (
        f' <draw:page draw:name="{name}" draw:style-name="{style}" draw:master-page-name="Default">\n'
        f'{body} </draw:page>\n'
    )


# Slide 1: a title, a code example with a tab, a three-space run and a line
# break, and a native table with repeated and merged cells.
CODE_PARAGRAPHS = (
    '<text:p>SELECT<text:tab/>nom,<text:s text:c="3"/>prix</text:p>'
    '<text:p>FROM<text:tab/>produit<text:line-break/>WHERE prix &gt; 10;</text:p>'
    '<text:p>    indentation gardee</text:p>'
)
NATIVE_TABLE = (
    '  <draw:frame draw:name="grille" svg:x="1cm" svg:y="13cm" svg:width="20cm" svg:height="5cm">'
    '<table:table table:name="T1">'
    '<table:table-row>'
    '<table:table-cell table:number-columns-repeated="5"><text:p>X</text:p></table:table-cell>'
    '<table:table-cell table:number-columns-spanned="2"><text:p>entete fusionnee</text:p></table:table-cell>'
    '<table:covered-table-cell/>'
    '</table:table-row>'
    '<table:table-row>'
    '<table:table-cell><text:p>a</text:p></table:table-cell>'
    '<table:table-cell><text:p>b</text:p></table:table-cell>'
    '</table:table-row>'
    '</table:table></draw:frame>\n'
)

SLIDE1 = (
    _frame('titre', x=1, y=1, presentation_class='title', body=_text_box('<text:p>Algebre relationnelle</text:p>'))
    + _frame('code', x=1, y=5, height=7, body=_text_box(CODE_PARAGRAPHS))
    + NATIVE_TABLE
)

# Slide 2 is hidden through an inherited style. Its picture and its notes must
# not reach the result.
SLIDE2_HIDDEN = (
    _frame('titre2', x=1, y=1, presentation_class='title', body=_text_box('<text:p>Page masquee</text:p>'))
    + _frame('secret', x=1, y=5, body='<draw:image xlink:href="Pictures/hidden.png"/>')
    + _notes('Notes secretes: cette ligne ne doit jamais sortir.')
)

# Slide 3: a group with two pictures, a labelled shape and a connector, an
# embedded sheet, a formula object and an embedded drawing.
SLIDE3 = (
    _frame('titre3', x=1, y=0.5, presentation_class='title', body=_text_box('<text:p>Graphes</text:p>'))
    + '  <draw:g draw:name="schema" svg:x="1cm" svg:y="4cm" svg:width="10cm" svg:height="5cm">'
      '<draw:frame draw:name="sous1" svg:x="1cm" svg:y="4cm" svg:width="4cm" svg:height="3cm">'
      '<draw:image xlink:href="Pictures/img1.png"/></draw:frame>'
      '<draw:frame draw:name="sous2" svg:x="5cm" svg:y="4cm" svg:width="4cm" svg:height="3cm">'
      '<draw:image xlink:href="Pictures/img2.png"/></draw:frame>'
      '<draw:custom-shape draw:name="etiquette" svg:x="1cm" svg:y="7cm" svg:width="3cm" svg:height="1cm">'
      '<text:p>Sommet initial</text:p></draw:custom-shape>'
      '<draw:connector draw:name="fleche" svg:x="4cm" svg:y="7.5cm" svg:width="2cm" svg:height="0.5cm"/>'
      '</draw:g>\n'
    # An embedded object frame also carries the picture the office suite drew
    # for it. Reading that picture instead of the object is how a table loses
    # its structure, so the fixture keeps both children.
    + _frame('feuille', x=1, y=9, height=3,
             body='<draw:object xlink:href="./Object 2"/>'
                  '<draw:image xlink:href="ObjectReplacements/Object 2"/>')
    + _frame('formule', x=1, y=12.5, height=2, body='<draw:object xlink:href="./Object 3"/>')
    + _frame('dessin', x=1, y=15, height=4,
             body='<draw:object xlink:href="./Object 4"/>'
                  '<draw:image xlink:href="ObjectReplacements/Object 4"/>')
)

# Slide 4: notes on a visible slide, a picture referenced over the network and
# an object this reader does not classify.
SLIDE4 = (
    _frame('titre4', x=1, y=1, presentation_class='title', body=_text_box('<text:p>Resume</text:p>'))
    + _frame('distant', x=1, y=5, body='<draw:image xlink:href="https://example.invalid/figure.png"/>')
    + '  <draw:control draw:name="bouton" svg:x="1cm" svg:y="9cm" svg:width="3cm" svg:height="1cm"/>\n'
    + _notes('Notes visibles: a ne pas publier non plus.')
)

OBJECT_ENTRIES = (
    ' <manifest:file-entry manifest:full-path="Object 2/" '
    'manifest:media-type="application/vnd.oasis.opendocument.spreadsheet"/>\n'
    ' <manifest:file-entry manifest:full-path="Object 2/content.xml" manifest:media-type="text/xml"/>\n'
    ' <manifest:file-entry manifest:full-path="Object 3/" '
    'manifest:media-type="application/vnd.oasis.opendocument.formula"/>\n'
    ' <manifest:file-entry manifest:full-path="Object 3/content.xml" manifest:media-type="text/xml"/>\n'
    ' <manifest:file-entry manifest:full-path="Object 4/" '
    'manifest:media-type="application/vnd.oasis.opendocument.graphics"/>\n'
    ' <manifest:file-entry manifest:full-path="Object 4/content.xml" manifest:media-type="text/xml"/>\n'
    ' <manifest:file-entry manifest:full-path="Pictures/img1.png" manifest:media-type="image/png"/>\n'
    ' <manifest:file-entry manifest:full-path="Pictures/img2.png" manifest:media-type="image/png"/>\n'
    ' <manifest:file-entry manifest:full-path="Pictures/hidden.png" manifest:media-type="image/png"/>\n'
    ' <manifest:file-entry manifest:full-path="Pictures/inside-odg.png" manifest:media-type="image/png"/>\n'
    ' <manifest:file-entry manifest:full-path="ObjectReplacements/Object 2" manifest:media-type="image/png"/>\n'
    ' <manifest:file-entry manifest:full-path="ObjectReplacements/Object 4" '
    'manifest:media-type="application/x-openoffice-gdimetafile"/>\n'
)


def _write(target: Path, entries: dict[str, bytes | str], *, mimetype: str | None = PRESENTATION_MIME) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        if mimetype is not None:
            archive.writestr(zipfile.ZipInfo('mimetype'), mimetype, compress_type=zipfile.ZIP_STORED)
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return target


def write_course_deck(target: Path) -> Path:
    """Four slides: one hidden, one with notes, pictures, a table, a sheet, a
    formula object and an embedded drawing."""
    content = (
        CONTENT_HEADER
        + _page('page1', 'dp-visible', SLIDE1)
        + _page('page2', 'dp-hidden-child', SLIDE2_HIDDEN)
        + _page('page3', 'dp-visible', SLIDE3)
        + _page('page4', 'dp-visible', SLIDE4)
        + CONTENT_FOOTER
    )
    return _write(target, {
        'META-INF/manifest.xml': MANIFEST.format(root_type=PRESENTATION_MIME, extra=OBJECT_ENTRIES),
        'content.xml': content,
        'styles.xml': STYLES,
        'Object 2/content.xml': SPREADSHEET_XML,
        'Object 3/content.xml': FORMULA_XML,
        'Object 4/content.xml': GRAPHICS_XML,
        'Pictures/img1.png': PNG_BYTES,
        'Pictures/img2.png': PNG_BYTES[:-1] + b'\x01',
        'Pictures/hidden.png': PNG_BYTES[:-1] + b'\x02',
        'Pictures/inside-odg.png': PNG_BYTES[:-1] + b'\x03',
        'ObjectReplacements/Object 2': PNG_BYTES[:-1] + b'\x04',
        # A metafile, not a picture anything can show.
        'ObjectReplacements/Object 4': b'\x01\x00\x09\x00\x00\x03 gdi metafile',
    })


def write_minimal(target: Path, *, slides: int = 2, notes: bool = False) -> Path:
    body = ''
    for number in range(1, slides + 1):
        page_body = _frame(f'titre{number}', x=1, y=1, presentation_class='title',
                           body=_text_box(f'<text:p>Diapositive numero {number}</text:p>'))
        if notes:
            page_body += _notes(f'Notes de la diapositive {number}')
        body += _page(f'page{number}', 'dp-visible', page_body)
    return _write(target, {
        'META-INF/manifest.xml': MANIFEST.format(root_type=PRESENTATION_MIME, extra=''),
        'content.xml': CONTENT_HEADER + body + CONTENT_FOOTER,
        'styles.xml': STYLES,
    })


def write_master_only_slide(target: Path) -> Path:
    """A visible slide whose own XML holds nothing: it shows the master page.

    The real corpus starts four decks this way.
    """
    body = _page('page1', 'dp-visible', '  <office:forms/>\n') + _page(
        'page2', 'dp-visible',
        _frame('titre', x=1, y=1, presentation_class='title',
               body=_text_box('<text:p>Une diapositive avec du contenu</text:p>')))
    return _write(target, {
        'META-INF/manifest.xml': MANIFEST.format(root_type=PRESENTATION_MIME, extra=''),
        'content.xml': CONTENT_HEADER + body + CONTENT_FOOTER,
        'styles.xml': STYLES,
    })


def write_wide_table(target: Path, *, repeat: int = 300) -> Path:
    """One table whose cells carry a large repeat count and real content."""
    table = (
        '  <draw:frame draw:name="large" svg:x="1cm" svg:y="2cm" svg:width="20cm" svg:height="10cm">'
        '<table:table table:name="T">'
        '<table:table-row>'
        f'<table:table-cell table:number-columns-repeated="{repeat}"><text:p>valeur</text:p></table:table-cell>'
        '</table:table-row>'
        '</table:table></draw:frame>\n'
    )
    return _write(target, {
        'META-INF/manifest.xml': MANIFEST.format(root_type=PRESENTATION_MIME, extra=''),
        'content.xml': CONTENT_HEADER + _page('page1', 'dp-visible', table) + CONTENT_FOOTER,
        'styles.xml': STYLES,
    })


def write_table_with_empty_rows(target: Path) -> Path:
    """A header row, two repeated empty rows, then a row with content.

    The empty rows are between content, so they are not padding: the row below
    them has to keep its row number.
    """
    table = (
        '  <draw:frame draw:name="trous" svg:x="1cm" svg:y="2cm" svg:width="20cm" svg:height="10cm">'
        '<table:table table:name="T">'
        '<table:table-row><table:table-cell><text:p>entete</text:p></table:table-cell></table:table-row>'
        '<table:table-row table:number-rows-repeated="2"><table:table-cell/></table:table-row>'
        '<table:table-row><table:table-cell><text:p>apres le trou</text:p></table:table-cell></table:table-row>'
        '<table:table-row table:number-rows-repeated="400"><table:table-cell/></table:table-row>'
        '</table:table></draw:frame>\n'
    )
    return _write(target, {
        'META-INF/manifest.xml': MANIFEST.format(root_type=PRESENTATION_MIME, extra=''),
        'content.xml': CONTENT_HEADER + _page('page1', 'dp-visible', table) + CONTENT_FOOTER,
        'styles.xml': STYLES,
    })


def write_table_with_repeated_rows(target: Path, *, repeat: int = 1000) -> Path:
    """One row with content, repeated a large number of times."""
    table = (
        '  <draw:frame draw:name="lignes" svg:x="1cm" svg:y="2cm" svg:width="20cm" svg:height="10cm">'
        '<table:table table:name="T">'
        f'<table:table-row table:number-rows-repeated="{repeat}">'
        '<table:table-cell><text:p>ligne</text:p></table:table-cell></table:table-row>'
        '</table:table></draw:frame>\n'
    )
    return _write(target, {
        'META-INF/manifest.xml': MANIFEST.format(root_type=PRESENTATION_MIME, extra=''),
        'content.xml': CONTENT_HEADER + _page('page1', 'dp-visible', table) + CONTENT_FOOTER,
        'styles.xml': STYLES,
    })


def write_space_run(target: Path, *, count: int = 100_000_000) -> Path:
    """A paragraph whose text:s asks for a very large run of spaces."""
    body = (
        '  <draw:frame draw:name="espaces" svg:x="1cm" svg:y="2cm" svg:width="20cm" svg:height="5cm">'
        '<draw:text-box>'
        f'<text:p>a<text:s text:c="{count}"/>b</text:p>'
        '</draw:text-box></draw:frame>\n'
    )
    return _write(target, {
        'META-INF/manifest.xml': MANIFEST.format(root_type=PRESENTATION_MIME, extra=''),
        'content.xml': CONTENT_HEADER + _page('page1', 'dp-visible', body) + CONTENT_FOOTER,
        'styles.xml': STYLES,
    })


def write_late_doctype(target: Path, *, comment_bytes: int = 9000) -> Path:
    """content.xml with the doctype pushed past any fixed-size header check."""
    content = (
        '<?xml version="1.0"?>\n'
        + '<!--' + 'x' * comment_bytes + '-->\n'
        + '<!DOCTYPE office:document-content [ <!ENTITY payload "leaked"> ]>\n'
        + CONTENT_HEADER.split('?>', 1)[1]
        + _page('page1', 'dp-visible',
                _frame('t', x=1, y=1, presentation_class='title', body=_text_box('<text:p>&payload;</text:p>')))
        + CONTENT_FOOTER
    )
    return _write(target, {
        'META-INF/manifest.xml': MANIFEST.format(root_type=PRESENTATION_MIME, extra=''),
        'content.xml': content,
        'styles.xml': STYLES,
    })


def write_utf16_doctype(target: Path) -> Path:
    """The same declaration, in an encoding a byte scan would not see."""
    content = (
        '<?xml version="1.0" encoding="UTF-16"?>'
        '<!DOCTYPE office:document-content [ <!ENTITY payload "leaked"> ]>'
        + CONTENT_HEADER.split('?>', 1)[1]
        + _page('page1', 'dp-visible',
                _frame('t', x=1, y=1, presentation_class='title', body=_text_box('<text:p>&payload;</text:p>')))
        + CONTENT_FOOTER
    )
    return _write(target, {
        'META-INF/manifest.xml': MANIFEST.format(root_type=PRESENTATION_MIME, extra=''),
        'content.xml': content.encode('utf-16'),
        'styles.xml': STYLES,
    })


def write_duplicate_entry(target: Path, *, alias: str = 'content.xml') -> Path:
    """A package that carries the same part twice."""
    body = CONTENT_HEADER + _page('page1', 'dp-visible', _frame(
        't', x=1, y=1, presentation_class='title', body=_text_box('<text:p>Vrai</text:p>'))) + CONTENT_FOOTER
    other = CONTENT_HEADER + _page('page1', 'dp-visible', _frame(
        't', x=1, y=1, presentation_class='title', body=_text_box('<text:p>Faux</text:p>'))) + CONTENT_FOOTER
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(zipfile.ZipInfo('mimetype'), PRESENTATION_MIME, compress_type=zipfile.ZIP_STORED)
        archive.writestr('META-INF/manifest.xml', MANIFEST.format(root_type=PRESENTATION_MIME, extra=''))
        archive.writestr('content.xml', body)
        archive.writestr(alias, other)
        archive.writestr('styles.xml', STYLES)
    return target


def write_symlink_entry(target: Path) -> Path:
    """A package with a member stored as a symlink."""
    import stat as stat_module

    body = CONTENT_HEADER + _page('page1', 'dp-visible', '') + CONTENT_FOOTER
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(zipfile.ZipInfo('mimetype'), PRESENTATION_MIME, compress_type=zipfile.ZIP_STORED)
        archive.writestr('META-INF/manifest.xml', MANIFEST.format(root_type=PRESENTATION_MIME, extra=''))
        archive.writestr('content.xml', body)
        archive.writestr('styles.xml', STYLES)
        link = zipfile.ZipInfo('Pictures/passwd.png')
        link.create_system = 3
        link.external_attr = (stat_module.S_IFLNK | 0o777) << 16
        archive.writestr(link, '/etc/passwd')
    return target


def write_loose_vector_shapes(target: Path) -> Path:
    """Three drawing shapes on the slide itself, held by no group."""
    body = (
        _frame('titre', x=1, y=1, presentation_class='title', body=_text_box('<text:p>Schema</text:p>'))
        + '  <draw:custom-shape draw:name="boite1" svg:x="1cm" svg:y="6cm" svg:width="3cm" svg:height="2cm"/>\n'
        '  <draw:connector draw:name="lien" svg:x="4cm" svg:y="7cm" svg:width="2cm" svg:height="0.2cm"/>\n'
        '  <draw:custom-shape draw:name="boite2" svg:x="7cm" svg:y="6cm" svg:width="3cm" svg:height="2cm"/>\n'
    )
    return _write(target, {
        'META-INF/manifest.xml': MANIFEST.format(root_type=PRESENTATION_MIME, extra=''),
        'content.xml': CONTENT_HEADER + _page('page1', 'dp-visible', body) + CONTENT_FOOTER,
        'styles.xml': STYLES,
    })


def write_mixed_masters(target: Path) -> Path:
    """Two masters with different page sizes, and one page on each."""
    styles = STYLES.replace(
        ' </office:automatic-styles>',
        '  <style:page-layout style:name="PM2">\n'
        '   <style:page-layout-properties fo:page-width="14cm" fo:page-height="10cm"/>\n'
        '  </style:page-layout>\n'
        ' </office:automatic-styles>',
    ).replace(
        '  <style:master-page style:name="Default" style:page-layout-name="PM1"/>',
        '  <style:master-page style:name="Default" style:page-layout-name="PM1"/>\n'
        '  <style:master-page style:name="Petite" style:page-layout-name="PM2"/>',
    )
    first = _page('page1', 'dp-visible', _frame('t1', x=1, y=1, presentation_class='title',
                                                body=_text_box('<text:p>Grande page</text:p>')))
    second = (
        ' <draw:page draw:name="page2" draw:style-name="dp-visible" draw:master-page-name="Petite">\n'
        + _frame('t2', x=1, y=1, presentation_class='title', body=_text_box('<text:p>Petite page</text:p>'))
        + ' </draw:page>\n'
    )
    third = (
        ' <draw:page draw:name="page3" draw:style-name="dp-visible" draw:master-page-name="Inconnue">\n'
        + _frame('t3', x=1, y=1, presentation_class='title', body=_text_box('<text:p>Master inconnu</text:p>'))
        + ' </draw:page>\n'
    )
    return _write(target, {
        'META-INF/manifest.xml': MANIFEST.format(root_type=PRESENTATION_MIME, extra=''),
        'content.xml': CONTENT_HEADER + first + second + third + CONTENT_FOOTER,
        'styles.xml': styles,
    })


def write_hidden_external_reference(target: Path) -> Path:
    """A link that exists only on a hidden slide and in speaker notes."""
    hidden = _page('page2', 'dp-hidden-parent',
                   _frame('secret', x=1, y=5, body='<draw:image xlink:href="https://hidden.invalid/plan.png"/>'))
    visible = _page('page1', 'dp-visible',
                    _frame('t', x=1, y=1, presentation_class='title',
                           body=_text_box('<text:p>Visible</text:p>'))
                    + _notes('Voir https://notes.invalid/secret')
                    + '  <presentation:notes draw:style-name="dp-visible">'
                      '<draw:frame svg:x="1cm" svg:y="1cm" svg:width="5cm" svg:height="5cm">'
                      '<draw:image xlink:href="https://notes.invalid/image.png"/></draw:frame>'
                      '</presentation:notes>\n')
    return _write(target, {
        'META-INF/manifest.xml': MANIFEST.format(root_type=PRESENTATION_MIME, extra=''),
        'content.xml': CONTENT_HEADER + visible + hidden + CONTENT_FOOTER,
        'styles.xml': STYLES,
    })


def write_active_content(target: Path) -> Path:
    """A deck with scripts, event listeners and links to files outside it."""
    body = (
        _frame('titre', x=1, y=1, presentation_class='title', body=_text_box('<text:p>Actif</text:p>'))
        + '  <draw:frame draw:name="local" svg:x="1cm" svg:y="5cm" svg:width="8cm" svg:height="5cm">'
          '<draw:image xlink:href="file:///etc/passwd"/></draw:frame>\n'
        + '  <draw:frame draw:name="distant" svg:x="1cm" svg:y="11cm" svg:width="8cm" svg:height="5cm">'
          '<draw:image xlink:href="https://example.invalid/pixel.png"/>'
          '<office:event-listeners><presentation:event-listener script:event-name="dom:click" '
          'xlink:href="file:///tmp/run.sh"/></office:event-listeners></draw:frame>\n'
    )
    content = (
        CONTENT_HEADER.replace(
            ' office:version="1.2">',
            ' xmlns:script="urn:oasis:names:tc:opendocument:xmlns:script:1.0" office:version="1.2">'
            '\n <office:scripts><office:script script:language="Basic">Shell("rm -rf /")</office:script>'
            '</office:scripts>',
        )
        + _page('page1', 'dp-visible', body)
        + CONTENT_FOOTER
    )
    return _write(target, {
        'META-INF/manifest.xml': MANIFEST.format(root_type=PRESENTATION_MIME, extra=''),
        'content.xml': content,
        'styles.xml': STYLES,
    })


def write_master_notes(target: Path) -> Path:
    """Notes on a visible slide and a notes block inside the master styles."""
    styles = STYLES.replace(
        '  <style:master-page style:name="Default" style:page-layout-name="PM1"/>',
        '  <style:master-page style:name="Default" style:page-layout-name="PM1">\n'
        '   <presentation:notes xmlns:presentation="urn:oasis:names:tc:opendocument:xmlns:presentation:1.0" '
        'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
        '<draw:frame><draw:text-box><text:p>Modele de notes a ne pas montrer</text:p>'
        '</draw:text-box></draw:frame></presentation:notes>\n'
        '  </style:master-page>',
    )
    visible = _page('page1', 'dp-visible',
                    _frame('t', x=1, y=1, presentation_class='title',
                           body=_text_box('<text:p>Avec notes</text:p>'))
                    + _notes('Note de diapositive a ne pas montrer'))
    return _write(target, {
        'META-INF/manifest.xml': MANIFEST.format(root_type=PRESENTATION_MIME, extra=''),
        'content.xml': CONTENT_HEADER + visible + CONTENT_FOOTER,
        'styles.xml': styles,
    })


def write_all_hidden(target: Path) -> Path:
    body = _page('page1', 'dp-hidden-parent',
                 _frame('t', x=1, y=1, presentation_class='title', body=_text_box('<text:p>Cachee</text:p>')))
    return _write(target, {
        'META-INF/manifest.xml': MANIFEST.format(root_type=PRESENTATION_MIME, extra=''),
        'content.xml': CONTENT_HEADER + body + CONTENT_FOOTER,
        'styles.xml': STYLES,
    })


def write_path_escape(target: Path) -> Path:
    return _write(target, {
        'META-INF/manifest.xml': MANIFEST.format(root_type=PRESENTATION_MIME, extra=''),
        'content.xml': CONTENT_HEADER + _page('page1', 'dp-visible', '') + CONTENT_FOOTER,
        'styles.xml': STYLES,
        '../escape.txt': 'must not be read',
    })


def write_doctype(target: Path) -> Path:
    content = (
        '<?xml version="1.0"?>\n<!DOCTYPE office:document-content [ <!ENTITY payload "boom"> ]>\n'
        + CONTENT_HEADER.split('?>', 1)[1]
        + _page('page1', 'dp-visible', '')
        + CONTENT_FOOTER
    )
    return _write(target, {
        'META-INF/manifest.xml': MANIFEST.format(root_type=PRESENTATION_MIME, extra=''),
        'content.xml': content,
        'styles.xml': STYLES,
    })


def write_zip_bomb(target: Path, *, payload_bytes: int = 4 * 1024 * 1024) -> Path:
    """One entry that expands far more than the ratio limit allows."""
    return _write(target, {
        'META-INF/manifest.xml': MANIFEST.format(root_type=PRESENTATION_MIME, extra=''),
        'content.xml': CONTENT_HEADER + _page('page1', 'dp-visible', '') + CONTENT_FOOTER,
        'styles.xml': STYLES,
        'Pictures/bomb.bin': b'\0' * payload_bytes,
    })


def write_wrong_type(target: Path) -> Path:
    """A package that declares itself a spreadsheet."""
    return _write(target, {
        'META-INF/manifest.xml': MANIFEST.format(
            root_type='application/vnd.oasis.opendocument.spreadsheet', extra=''),
        'content.xml': CONTENT_HEADER + _page('page1', 'dp-visible', '') + CONTENT_FOOTER,
        'styles.xml': STYLES,
    }, mimetype='application/vnd.oasis.opendocument.spreadsheet')
