import { useParams, useNavigate } from 'react-router-dom'

export default function ClaimSubmitted() {
  const { claimId } = useParams()
  const navigate = useNavigate()

  return (
    <div className="max-w-xl mx-auto mt-10">
      <div className="bg-slate-900 rounded-2xl border border-slate-700 p-8 text-center">
        <div className="w-16 h-16 rounded-full bg-green-500/20 text-green-400 text-3xl flex items-center justify-center mx-auto mb-5">
          ✓
        </div>
        <h1 className="text-2xl font-bold text-white mb-2">Claim Submitted</h1>
        <p className="text-slate-400 text-sm mb-5 leading-relaxed">
          Thank you. Your claim has been received and is now <span className="text-amber-300 font-medium">under review</span> by
          our claims team. You'll be contacted once a decision is made.
        </p>

        {claimId && (
          <div className="bg-slate-800/60 rounded-xl px-4 py-3 mb-6 inline-block">
            <p className="text-xs text-slate-500">Reference Number</p>
            <p className="text-lg font-mono font-semibold text-white">{claimId}</p>
          </div>
        )}

        <div>
          <button
            onClick={() => navigate('/new')}
            className="px-5 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white font-semibold rounded-xl transition-colors"
          >
            File Another Claim
          </button>
        </div>
      </div>
    </div>
  )
}
