/** Open the complete source PDF at the physical page resolved by a citation. */
import { useParams, useSearchParams } from 'react-router-dom';
import { PdfFrame } from '../components/PdfPreview';
import { useSourcePdf } from '../lib/sourcePdf';

export default function SourcePdf() {
  const { documentId } = useParams();
  const [searchParams] = useSearchParams();
  const sessionId = searchParams.get('session_id');
  const revisionId = searchParams.get('revision_id');
  const requestedPage = Number(searchParams.get('page'));
  const page = Number.isSafeInteger(requestedPage) && requestedPage > 0 ? requestedPage : 1;
  const { pdfUrl, error } = useSourcePdf(documentId, sessionId, revisionId);

  if (!sessionId) return <p className="p-6 text-cream">Missing chat session.</p>;
  return (
    <div className="flex h-full min-h-0 flex-col text-cream">
      <div className="flex shrink-0 items-center gap-3 border-b border-border-default px-4 py-2">
        <span className="font-medium">Course PDF</span>
        <span className="text-cream-secondary">Opened at PDF page {page}. Use the PDF viewer to browse the document.</span>
      </div>
      <PdfFrame pdfUrl={pdfUrl} error={error} page={page} />
    </div>
  );
}
