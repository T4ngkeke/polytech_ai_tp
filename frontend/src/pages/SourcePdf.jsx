/** Open the complete source PDF at the physical page resolved by a citation. */
import { useParams, useSearchParams } from 'react-router-dom';
import { PdfFrame } from '../components/PdfPreview';
import { useSourcePdf } from '../lib/sourcePdf';
import { useT } from '../lib/i18n';

export default function SourcePdf() {
  const t = useT();
  const { documentId } = useParams();
  const [searchParams] = useSearchParams();
  const sessionId = searchParams.get('session_id');
  const revisionId = searchParams.get('revision_id');
  const requestedPage = Number(searchParams.get('page'));
  const page = Number.isSafeInteger(requestedPage) && requestedPage > 0 ? requestedPage : 1;
  const { pdfUrl, error } = useSourcePdf(documentId, sessionId, revisionId);

  if (!sessionId) return <p className="p-6 text-[14px] text-cream">{t('p.pdf.missingSession')}</p>;
  return (
    <div className="flex h-full min-h-0 flex-col text-cream">
      <div className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 border-b border-border-subtle px-4 py-2 text-[14px]">
        <span className="font-semibold">{t('p.pdf.title')}</span>
        <span className="text-[13px] text-cream-muted">{t('p.pdf.opened', { page })}</span>
      </div>
      <PdfFrame pdfUrl={pdfUrl} error={error} page={page} />
    </div>
  );
}
