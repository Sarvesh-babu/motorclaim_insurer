import { useState, useCallback, useRef, useEffect } from 'react'
import api from '../utils/api'

export function useInvestigationSSE(claimId, onDone) {
  const [agentStatuses, setAgentStatuses] = useState({})
  const [liveAgents, setLiveAgents] = useState({})
  const [investigating, setInvestigating] = useState(false)
  const esRef = useRef(null)

  // Reset on claim switch (and on unmount): close any open stream and clear
  // stale agent results so one claim's live findings never bleed into another.
  useEffect(() => {
    setAgentStatuses({})
    setLiveAgents({})
    setInvestigating(false)
    return () => {
      if (esRef.current) {
        esRef.current.close()
        esRef.current = null
      }
    }
  }, [claimId])

  const start = useCallback(async () => {
    if (esRef.current) esRef.current.close()
    setInvestigating(true)
    setAgentStatuses({})
    setLiveAgents({})

    await api.post(`/claims/${claimId}/investigate`)

    const es = new EventSource(`/api/claims/${claimId}/stream`)
    esRef.current = es

    es.onmessage = (e) => {
      const event = JSON.parse(e.data)
      if (event.type === 'agent_start') {
        setAgentStatuses(s => ({ ...s, [event.agent]: 'running' }))
      } else if (event.type === 'agent_done') {
        setAgentStatuses(s => ({ ...s, [event.agent]: 'done' }))
        setLiveAgents(a => ({ ...a, [event.agent]: event.result }))
      } else if (event.type === 'agent_error') {
        setAgentStatuses(s => ({ ...s, [event.agent]: 'error' }))
      } else if (event.type === 'done') {
        setInvestigating(false)
        es.close()
        onDone?.()
      }
    }
    es.onerror = () => {
      setInvestigating(false)
      es.close()
    }
  }, [claimId, onDone])

  return { agentStatuses, liveAgents, investigating, start }
}
