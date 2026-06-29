import { useState } from 'react'
import VisualEvidenceAnalysis from './VisualEvidenceAnalysis'

function Tab({ label, active, onClick }) {
  return (
    <button
      onClick={onClick}
      className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
        active
          ? 'border-indigo-500 text-indigo-400'
          : 'border-transparent text-slate-500 hover:text-slate-300'
      }`}
    >
      {label}
    </button>
  )
}

// ── KB Source Pill with hover tooltip ────────────────────────────────────────
function KBPill({ text, color = 'indigo' }) {
  const [open, setOpen] = useState(false)
  const colors = {
    indigo: 'bg-indigo-500/15 text-indigo-300 border-indigo-500/30 hover:bg-indigo-500/25',
    amber:  'bg-amber-500/15  text-amber-300  border-amber-500/30  hover:bg-amber-500/25',
    green:  'bg-green-500/15  text-green-300  border-green-500/30  hover:bg-green-500/25',
  }
  const label = text.split(' — ')[0] || text.slice(0, 60)

  return (
    <div className="relative inline-block">
      <button
        onClick={() => setOpen(o => !o)}
        className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium border transition-colors cursor-pointer ${colors[color]}`}
      >
        <span className="text-[10px]">📎</span>
        {label}
      </button>
      {open && (
        <div className="absolute z-20 bottom-full mb-2 left-0 w-80 bg-slate-800 border border-slate-600 rounded-xl p-3 shadow-xl text-xs text-slate-300 leading-relaxed">
          {text}
          <button
            onClick={() => setOpen(false)}
            className="absolute top-2 right-2 text-slate-500 hover:text-slate-300 text-xs"
          >✕</button>
        </div>
      )}
    </div>
  )
}

function KBSources({ settlement, fraudAgent }) {
  const precedents  = settlement?.kb_precedents_applied   || []
  const schemes     = fraudAgent?.matched_schemes          || []
  const refs        = fraudAgent?.kb_references            || []
  const all = [...precedents, ...schemes, ...refs]
  if (!all.length) return null

  return (
    <div className="mt-5 pt-4 border-t border-slate-700">
      <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">
        KB Sources Used
      </p>
      <div className="flex flex-wrap gap-2">
        {precedents.map((t, i) => <KBPill key={`p${i}`} text={t} color="green" />)}
        {schemes.map((t, i)    => <KBPill key={`s${i}`} text={t} color="amber" />)}
        {refs.map((t, i)       => <KBPill key={`r${i}`} text={t} color="indigo" />)}
      </div>
    </div>
  )
}

function ReasoningTrail({ trail, settlement, fraudAgent }) {
  if (!trail?.length) return <p className="text-slate-500 text-sm">No reasoning trail available yet.</p>
  return (
    <div>
      <ol className="space-y-3">
        {trail.map((step, i) => (
          <li key={i} className="flex gap-3">
            <div className="flex-shrink-0 w-6 h-6 rounded-full bg-indigo-600/30 text-indigo-400 flex items-center justify-center text-xs font-bold">
              {i + 1}
            </div>
            <p className="text-sm text-slate-300 pt-0.5">{step}</p>
          </li>
        ))}
      </ol>
      <KBSources settlement={settlement} fraudAgent={fraudAgent} />
    </div>
  )
}

function Timeline({ agents }) {
  const AGENT_LABELS = {
    damage_assessment: 'Damage Assessment',
    fraud_intelligence: 'Fraud Intelligence',
    incident_reconstruction: 'Incident Reconstruction',
    context_verification: 'Context Verification',
    settlement_recommendation: 'Settlement Recommendation',
  }
  const entries = Object.entries(agents || {})
  if (!entries.length) return <p className="text-slate-500 text-sm">No timeline data yet.</p>

  return (
    <div className="space-y-3">
      {entries.map(([key, data], i) => (
        <div key={key} className="flex gap-3 items-start">
          <div className="flex flex-col items-center">
            <div className={`w-2.5 h-2.5 rounded-full mt-1 ${data.status === 'completed' ? 'bg-green-500' : 'bg-red-500'}`} />
            {i < entries.length - 1 && <div className="w-0.5 h-6 bg-slate-700 mt-1" />}
          </div>
          <div className="flex-1">
            <p className="text-sm font-medium text-white">{AGENT_LABELS[key] || key}</p>
            <p className="text-xs text-slate-400">{data.summary || data.status}</p>
          </div>
        </div>
      ))}
    </div>
  )
}

// ── Documents Tab ─────────────────────────────────────────────────────────────

function GarageXCheck({ damage }) {
  if (!damage?.garage_estimate_provided) return null
  const variance = damage.garage_vs_ai_variance_pct
  const isInflation = damage.garage_inflation_flag
  const isUnder = variance !== null && variance < -35

  const bgClass = isInflation
    ? 'bg-red-500/10 border-red-500/30'
    : isUnder
    ? 'bg-amber-500/10 border-amber-500/30'
    : 'bg-slate-800/40 border-slate-700'

  const textClass = isInflation ? 'text-red-300' : isUnder ? 'text-amber-300' : 'text-slate-300'

  return (
    <div className={`rounded-lg p-3 border mt-3 ${bgClass}`}>
      <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1.5">
        AI vs Garage Cross-Check
      </p>
      <p className={`text-sm ${textClass}`}>{damage.garage_inflation_note}</p>
      {variance !== null && (
        <div className="flex items-center gap-2 mt-2">
          <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
            isInflation ? 'bg-red-500/20 text-red-400' :
            isUnder ? 'bg-amber-500/20 text-amber-400' :
            'bg-green-500/20 text-green-400'
          }`}>
            {variance > 0 ? '+' : ''}{variance}%{' '}
            {isInflation ? 'Inflation flagged' : isUnder ? 'Underdeclaration' : 'Within normal range'}
          </span>
        </div>
      )}
    </div>
  )
}

const DOC_SKIP_KEYS = new Set(['doc_type', 'parsed_ok', 'error', 'line_items'])

function DocKV({ label, value }) {
  if (!value && value !== 0) return null
  return (
    <div className="flex justify-between items-start py-1.5 border-b border-slate-700/50 last:border-0 gap-3">
      <span className="text-xs text-slate-500 capitalize shrink-0">{label.replace(/_/g, ' ')}</span>
      <span className="text-xs text-slate-300 text-right">{String(value)}</span>
    </div>
  )
}

function DocCard({ title, icon, parsed, fileUrls }) {
  const hasFiles = fileUrls?.length > 0
  const hasParsed = parsed?.parsed_ok === true
  if (!parsed && !hasFiles) return null

  return (
    <div className="bg-slate-800/60 rounded-xl border border-slate-700 p-4 mb-3">
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="text-base">{icon}</span>
          <h4 className="text-sm font-semibold text-white">{title}</h4>
        </div>
        <div className="flex items-center gap-2">
          {hasFiles && (
            <span className="text-xs bg-indigo-500/20 text-indigo-400 px-2 py-0.5 rounded-full">
              {fileUrls.length} file{fileUrls.length > 1 ? 's' : ''}
            </span>
          )}
          {parsed && (
            <span className={`text-xs px-2 py-0.5 rounded-full ${
              hasParsed ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'
            }`}>
              {hasParsed ? 'AI Parsed' : 'Parse failed'}
            </span>
          )}
        </div>
      </div>

      {parsed?.error && (
        <p className="text-xs text-red-400 mb-2">{parsed.error}</p>
      )}

      {hasParsed && (
        <div>
          {Object.entries(parsed)
            .filter(([k, v]) => !DOC_SKIP_KEYS.has(k) && v !== null && v !== undefined && v !== '')
            .map(([k, v]) => (
              <DocKV key={k} label={k} value={v} />
            ))}

          {parsed.line_items?.length > 0 && (
            <div className="mt-3 pt-2 border-t border-slate-700/50">
              <p className="text-xs text-slate-500 uppercase tracking-wider mb-2">Line Items</p>
              <div className="space-y-1">
                {parsed.line_items.map((item, i) => (
                  <div key={i} className="flex justify-between text-xs py-1 border-b border-slate-700/30 last:border-0">
                    <span className="text-slate-300">{item.part}</span>
                    <div className="flex items-center gap-3">
                      <span className="text-slate-500">{item.type}</span>
                      <span className="text-slate-300 font-mono">₹{Number(item.amount).toLocaleString('en-IN')}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function DashCamCard({ fileUrls, frameUrls }) {
  const hasFiles = fileUrls?.length > 0
  if (!hasFiles) return null
  const extracted = frameUrls?.length > 0

  return (
    <div className="bg-slate-800/60 rounded-xl border border-slate-700 p-4 mb-3">
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="text-base">🎥</span>
          <h4 className="text-sm font-semibold text-white">Dash Cam Video</h4>
        </div>
        <span className={`text-xs px-2 py-0.5 rounded-full ${
          extracted ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'
        }`}>
          {extracted ? `${frameUrls.length} frame${frameUrls.length > 1 ? 's' : ''} extracted` : 'Extraction failed'}
        </span>
      </div>

      {extracted ? (
        <div className="grid grid-cols-4 gap-2">
          {frameUrls.map((url, i) => (
            <img key={i} src={url} alt={`Dash cam frame ${i + 1}`}
                 className="w-full h-16 object-cover rounded-lg border border-slate-700" />
          ))}
        </div>
      ) : (
        <p className="text-xs text-red-400">
          Video uploaded — frames could not be extracted (unsupported/corrupt file).
        </p>
      )}
    </div>
  )
}

function DocumentsTab({ claimDocs, agents }) {
  const parsed = claimDocs?.parsed || {}
  const files = claimDocs?.files || {}
  const dashcamFrames = claimDocs?.dashcam_frames || []
  const damage = agents?.damage_assessment

  const hasAnything = parsed.estimate || parsed.fir || files.estimate?.length || files.fir?.length || files.dashcam?.length

  if (claimDocs === undefined) {
    return <p className="text-slate-500 text-sm">Loading documents...</p>
  }

  if (!hasAnything && !damage?.garage_estimate_provided) {
    return (
      <div className="text-center py-8">
        <p className="text-2xl mb-2">📂</p>
        <p className="text-slate-400 text-sm font-medium mb-1">No supporting documents</p>
        <p className="text-slate-500 text-xs">Upload a garage estimate, FIR or dash-cam clip when filing a new claim to enable document cross-checks.</p>
      </div>
    )
  }

  return (
    <div>
      <GarageXCheck damage={damage} />
      <div className={damage?.garage_estimate_provided ? 'mt-4' : ''}>
        <DocCard
          title="Garage Repair Estimate"
          icon="🧾"
          parsed={parsed.estimate}
          fileUrls={files.estimate}
        />
        <DocCard
          title="First Information Report (FIR)"
          icon="📋"
          parsed={parsed.fir}
          fileUrls={files.fir}
        />
        <DashCamCard fileUrls={files.dashcam} frameUrls={dashcamFrames} />
      </div>
    </div>
  )
}

// ── Main Component ────────────────────────────────────────────────────────────

export default function EvidencePanel({ imageUrls, agents, settlement, onDeleteImage, claimDocs }) {
  const [tab, setTab] = useState('evidence')

  const damageAgent  = agents?.damage_assessment
  const damagedParts = damageAgent?.damaged_parts || []
  const trail        = settlement?.reasoning_trail || []
  const fraudAgent   = agents?.fraud_intelligence
  // Only show an affirmative "no damage" once the damage agent has actually run.
  const noDamage = !!damageAgent && damageAgent.status === 'completed' && damagedParts.length === 0

  return (
    <div className="bg-slate-900 rounded-2xl border border-slate-700 p-5">
      <div className="flex border-b border-slate-700 mb-4 -mt-1">
        <Tab label="Evidence"        active={tab === 'evidence'}  onClick={() => setTab('evidence')} />
        <Tab label="Documents"       active={tab === 'documents'} onClick={() => setTab('documents')} />
        <Tab label="Reasoning Trail" active={tab === 'reasoning'} onClick={() => setTab('reasoning')} />
        <Tab label="Timeline"        active={tab === 'timeline'}  onClick={() => setTab('timeline')} />
      </div>

      {tab === 'evidence' && (
        <>
          <VisualEvidenceAnalysis
            imageUrls={imageUrls}
            damagedParts={damagedParts}
            noDamage={noDamage}
            noDamageReason={damageAgent?.no_damage_reason}
            onDeleteImage={onDeleteImage}
          />
          <KBSources settlement={settlement} fraudAgent={fraudAgent} />
        </>
      )}

      {tab === 'documents' && (
        <DocumentsTab claimDocs={claimDocs} agents={agents} />
      )}

      {tab === 'reasoning' && (
        <ReasoningTrail trail={trail} settlement={settlement} fraudAgent={fraudAgent} />
      )}

      {tab === 'timeline' && (
        <Timeline agents={agents} />
      )}
    </div>
  )
}
