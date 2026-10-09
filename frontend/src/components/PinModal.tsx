import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import QRCode from 'qrcode'
import { Link } from 'react-router-dom'
import { cameras, pairing, type PairingCodeMinted } from '../lib/api'
import { errorMessage } from '../lib/errors'
import { Dialog } from './Dialog'

interface PinModalProps {
  open: boolean
  sessionId: string
  replaceCameraId?: string
  replaceCameraName?: string
  onClose: () => void
}

function secondsLeft(iso: string, now: number): number {
  return Math.max(0, Math.round((new Date(iso).getTime() - now) / 1000))
}

/** Mints a pairing code and shows PIN, countdown, URL and QR (J.2). */
export function PinModal({
  open,
  sessionId,
  replaceCameraId,
  replaceCameraName,
  onClose,
}: PinModalProps) {
  const queryClient = useQueryClient()
  const [code, setCode] = useState<PairingCodeMinted | null>(null)
  const [qr, setQr] = useState<string | null>(null)
  const [now, setNow] = useState(() => Date.now())
  const [copied, setCopied] = useState(false)

  const mint = useMutation({
    mutationFn: () =>
      pairing.mint(sessionId, replaceCameraId ? { replace_camera_id: replaceCameraId } : {}),
    onSuccess: (minted) => {
      setCode(minted)
      setCopied(false)
    },
  })
  const cancel = useMutation({
    mutationFn: (codeId: string) => pairing.cancel(codeId),
    onSettled: () => {
      setCode(null)
      onClose()
    },
  })

  // Mint on open. The parent remounts this component per opening (key), so the
  // previous PIN never survives a close.
  const mintNow = mint.mutate
  useEffect(() => {
    if (open) mintNow()
  }, [open, mintNow])

  useEffect(() => {
    if (!code) return
    let cancelled = false
    QRCode.toDataURL(code.pair_url, { margin: 1, width: 400, errorCorrectionLevel: 'M' })
      .then((url) => {
        if (!cancelled) setQr(url)
      })
      .catch(() => setQr(null))
    return () => {
      cancelled = true
    }
  }, [code])

  useEffect(() => {
    if (!open) return
    const id = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [open])

  // Poll the code list to notice redemption (also pushed as camera_status over the socket).
  const codes = useQuery({
    queryKey: ['pairing-codes', sessionId],
    queryFn: () => pairing.list(sessionId),
    enabled: open && code !== null,
    refetchInterval: 2000,
  })
  const row = codes.data?.find((c) => c.code_id === code?.code_id)
  const redeemedCameraId = row?.status === 'redeemed' ? row.redeemed_camera_id : null
  const camera = useQuery({
    queryKey: ['camera', redeemedCameraId],
    queryFn: () => cameras.get(redeemedCameraId!),
    enabled: redeemedCameraId !== null,
  })
  useEffect(() => {
    if (redeemedCameraId) {
      void queryClient.invalidateQueries({ queryKey: ['session', sessionId] })
      void queryClient.invalidateQueries({ queryKey: ['sessions'] })
    }
  }, [redeemedCameraId, queryClient, sessionId])

  const left = code ? secondsLeft(code.expires_at, now) : 0
  const expired = code !== null && (left === 0 || row?.status === 'expired')
  const locked = row?.status === 'locked'
  const title = replaceCameraId
    ? `Replace phone for ${replaceCameraName ?? 'camera'}`
    : 'Add camera'

  const copy = async () => {
    if (!code) return
    try {
      await navigator.clipboard.writeText(code.pair_url)
      setCopied(true)
    } catch {
      setCopied(false)
    }
  }

  return (
    <Dialog open={open} title={title} onClose={onClose}>
      {mint.isPending && <p className="muted">Minting a pairing code…</p>}
      {mint.isError && (
        <>
          <p className="form-error" role="alert">
            {errorMessage(mint.error)}
          </p>
          <div className="dialog-actions">
            <button type="button" className="btn" onClick={onClose}>
              Close
            </button>
            <button type="button" className="btn btn-primary" onClick={() => mint.mutate()}>
              Try again
            </button>
          </div>
        </>
      )}
      {code && redeemedCameraId && (
        <>
          <p>
            <span aria-hidden="true">✓ </span>
            Phone connected: <strong>{camera.data?.name ?? '…'}</strong>
          </p>
          <div className="dialog-actions">
            <button type="button" className="btn" onClick={onClose}>
              Close
            </button>
            <Link className="btn btn-primary" to={`/cameras/${redeemedCameraId}`}>
              Open camera
            </Link>
          </div>
        </>
      )}
      {code && !redeemedCameraId && (
        <>
          <p className="muted small">
            On the phone, open the pairing page and type this code, or scan the QR.
          </p>
          <div className="pin" aria-label={`PIN ${code.pin.split('').join(' ')}`}>
            {code.pin}
          </div>
          <div className="pin-meta" aria-live="polite">
            {locked ? (
              <span className="warn">Locked after too many wrong attempts. Regenerate.</span>
            ) : expired ? (
              <span className="warn">Expired, mint a new one.</span>
            ) : (
              <span>
                Expires in {Math.floor(left / 60)}:{String(left % 60).padStart(2, '0')}
                {row && row.attempts > 0
                  ? ` · ${row.attempts}/${row.max_attempts} wrong attempts`
                  : ''}
              </span>
            )}
            {qr && <img className="qr" src={qr} alt="QR code of the pairing URL" />}
            <span className="pin-url">{code.pair_url}</span>
          </div>
          <div className="dialog-actions">
            <button type="button" className="btn" onClick={() => void copy()}>
              {copied ? 'Copied' : 'Copy link'}
            </button>
            <button
              type="button"
              className="btn"
              onClick={() => cancel.mutate(code.code_id)}
              disabled={cancel.isPending}
            >
              Cancel code
            </button>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => mint.mutate()}
              disabled={mint.isPending}
            >
              Regenerate
            </button>
          </div>
        </>
      )}
    </Dialog>
  )
}
