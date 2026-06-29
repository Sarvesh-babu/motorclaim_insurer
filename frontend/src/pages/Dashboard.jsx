import { useCallback, useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import api from '../utils/api'

function DownloadReportButton({ claimId, hasResult }) {
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState('')

  if (!hasResult) return null

  const handleDownload = async () => {
    setLoading(true)
    setError('')
    try {
      const resp = await fetch(`/api/claims/${claimId}/report`)
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}))
        throw new Error(err.detail || 'Failed to generate report')
      }
      const blob = await resp.blob()
      const url  = URL.createObjectURL(blob)
      const a    = document.createElement('a')
      a.href     = url
      a.download = `ClaimIntel_Report_${claimId}.pdf`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex items-center gap-2">
      <button
        onClick={handleDownload}
        disabled={loading}
        className="inline-flex items-center gap-2 px-4 py-1.5 rounded-xl bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white text-sm font-semibold transition-colors shadow-sm"
      >
        {loading ? (
          <>
            <span className="w-3.5 h-3.5 rounded-full border-2 border-white/30 border-t-white animate-spin" />
            Generating PDF…
          </>
        ) : (
          <>
            <span>⬇</span>
            Download Report
          </>
        )}
      </button>
      {error && <span className="text-xs text-red-400">{error}</span>}
    </div>
  )
}
import { useInvestigationSSE } from '../hooks/useInvestigationSSE'
import ClaimOverview from '../components/ClaimOverview'
import InvestigationWorkflow from '../components/InvestigationWorkflow'
import InvestigationSummary from '../components/InvestigationSummary'
import StoryboardPanel from '../components/StoryboardPanel'
import ClaimDecision from '../components/ClaimDecision'
import EvidencePanel from '../components/EvidencePanel'
import AdjusterPanel from '../components/AdjusterPanel'
import DecisionLetter from '../components/DecisionLetter'
import { SkeletonDashboard } from '../components/Skeleton'

function ReviewBanner({ summary }) {
  const needsReview = summary?.needs_human_review
  const imgQ = summary?.image_quality
  const imgIssue = imgQ && imgQ.resubmit_recommended
  if (!needsReview && !imgIssue) return null

  return (
    <div className="space-y-2">
      {needsReview && (
        <div className="rounded-xl border border-amber-500/40 bg-amber-500/10 px-4 py-3">
          <p className="text-sm font-semibold text-amber-300 mb-1">⚠ Routed for human review</p>
          <ul className="list-disc list-inside text-xs text-amber-200/90 space-y-0.5">
            {(summary.routing_reasons || []).map((r, i) => <li key={i}>{r}</li>)}
          </ul>
        </div>
      )}
      {imgIssue && (
        <div className="rounded-xl border border-red-500/40 bg-red-500/10 px-4 py-3">
          <p className="text-sm font-semibold text-red-300 mb-1">📷 Image quality — resubmission recommended</p>
          <ul className="list-disc list-inside text-xs text-red-200/90 space-y-0.5">
            {(imgQ.issues || []).map((r, i) => <li key={i}>{r}</li>)}
          </ul>
        </div>
      )}
    </div>
  )
}

export default function Dashboard() {
  const { claimId } = useParams()
  const [claim, setClaim] = useState(null)
  const [policy, setPolicy] = useState(null)
  const [result, setResult] = useState(null)
  const [imageUrls, setImageUrls] = useState([])
  const [claimDocs, setClaimDocs] = useState(undefined)
  const [loading, setLoading] = useState(true)
  const [apiError, setApiError] = useState('')

  const fetchResult = useCallback(() => {
    // Re-fetch both the claim/result AND docs — investigation parses
    // FIR/estimate/telematics and extracts dash-cam frames during the run,
    // so the Documents tab needs fresh data too, not just the agent results.
    api.get(`/claims/${claimId}`)
      .then(r => {
        setClaim(r.data.claim)
        setResult(r.data.result)
        if (r.data.policy) setPolicy(r.data.policy)
      })
      .catch(() => {})
    api.get(`/claims/${claimId}/docs`)
      .then(r => setClaimDocs(r.data))
      .catch(() => {})
  }, [claimId])

  useEffect(() => {
    // Clean slate for each claim — prevents a previously-viewed claim's photos,
    // result or docs from lingering when switching between claims.
    setLoading(true)
    setApiError('')
    setClaim(null)
    setResult(null)
    setPolicy(null)
    setImageUrls([])
    setClaimDocs(undefined)

    Promise.all([
      api.get(`/claims/${claimId}`),
      api.get(`/claims/${claimId}/images`).catch(() => ({ data: { images: [] } })),
      api.get(`/claims/${claimId}/docs`).catch(() => ({ data: { parsed: {}, files: {} } })),
    ])
      .then(([claimRes, imgRes, docsRes]) => {
        setClaim(claimRes.data.claim)
        setResult(claimRes.data.result)
        if (claimRes.data.policy) setPolicy(claimRes.data.policy)
        setImageUrls(imgRes.data.images || [])
        setClaimDocs(docsRes.data)
      })
      .catch(() => setApiError('Claim not found or backend is unreachable.'))
      .finally(() => setLoading(false))
  }, [claimId])

  const { agentStatuses, liveAgents, investigating, start } = useInvestigationSSE(claimId, fetchResult)

  const handleDeleteImage = useCallback((url) => {
    const filename = url.split('/').pop()
    api.delete(`/claims/${claimId}/images/${encodeURIComponent(filename)}`)
      .then(() => setImageUrls(prev => prev.filter(u => u !== url)))
      .catch(() => {})
  }, [claimId])

  if (loading) return <SkeletonDashboard />

  if (apiError || !claim) {
    return (
      <div className="flex flex-col items-center justify-center h-80 text-center">
        <p className="text-4xl mb-4">🔍</p>
        <p className="text-red-400 font-medium mb-2">{apiError || 'Claim not found'}</p>
        <Link to="/" className="text-indigo-400 hover:underline text-sm mt-2">← Back to Claims Queue</Link>
      </div>
    )
  }

  const agents = { ...result?.agents, ...liveAgents }
  const summary = result?.summary || {}
  const hasResult = !!summary.decision
  const settlement = agents.settlement_recommendation

  return (
    <div className="max-w-7xl mx-auto space-y-4">

      <div className="flex items-center justify-between">
        <Link to="/" className="text-slate-500 hover:text-slate-300 text-sm transition-colors">
          ← Claims Queue
        </Link>
        <DownloadReportButton claimId={claimId} hasResult={hasResult} />
      </div>

      {/* Routing / data-quality alerts (#6) */}
      {hasResult && <ReviewBanner summary={summary} />}

      {/* Row 1: 3 columns */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <ClaimOverview claim={claim} policy={policy} />
        <InvestigationWorkflow
          agentStatuses={agentStatuses}
          liveAgents={agents}
          onStart={start}
          investigating={investigating}
          hasResult={hasResult}
        />
        <InvestigationSummary summary={summary} />
      </div>

      {/* Row 2: full-width storyboard (only when incident_reconstruction has storyboard data) */}
      {agents.incident_reconstruction?.storyboard_panels?.length > 0 && (
        <StoryboardPanel reconstructionData={agents.incident_reconstruction} />
      )}

      {/* Row 3: 2 columns */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <ClaimDecision summary={summary} settlementData={settlement} />
        <EvidencePanel
          imageUrls={imageUrls}
          agents={agents}
          settlement={settlement}
          onDeleteImage={handleDeleteImage}
          claimDocs={claimDocs}
        />
      </div>

      {/* Row 4: human-in-the-loop (#1) + customer letter (#7) */}
      {hasResult && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <AdjusterPanel
            claimId={claimId}
            result={result}
            aiDecision={summary.decision}
            onUpdated={fetchResult}
          />
          <DecisionLetter claimId={claimId} />
        </div>
      )}

    </div>
  )
}
