import { useState, type FormEvent } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { cameras, type CameraSettings } from '../lib/api'
import { errorMessage } from '../lib/errors'

export function SettingsForm({
  cameraId,
  settings,
}: {
  cameraId: string
  settings: CameraSettings
}) {
  const queryClient = useQueryClient()
  const [form, setForm] = useState<CameraSettings>(settings)
  const save = useMutation({
    mutationFn: () => cameras.patch(cameraId, { settings: form }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['camera', cameraId] }),
  })
  const num = (key: keyof CameraSettings) => (e: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [key]: Number(e.target.value) }))
  const submit = (e: FormEvent) => {
    e.preventDefault()
    save.mutate()
  }
  return (
    <form className="form" onSubmit={submit} aria-label="Camera settings">
      <div className="form-row">
        <label>
          Target fps (1–30)
          <input
            type="number"
            min={1}
            max={30}
            value={form.target_fps}
            onChange={num('target_fps')}
          />
        </label>
        <label>
          JPEG quality (30–95)
          <input
            type="number"
            min={30}
            max={95}
            value={form.jpeg_quality}
            onChange={num('jpeg_quality')}
          />
        </label>
      </div>
      <div className="form-row">
        <label>
          Max long edge (640–1920)
          <input
            type="number"
            min={640}
            max={1920}
            step={10}
            value={form.max_long_edge}
            onChange={num('max_long_edge')}
          />
        </label>
        <label>
          Confidence (0.05–0.95)
          <input
            type="number"
            min={0.05}
            max={0.95}
            step={0.05}
            value={form.confidence}
            onChange={num('confidence')}
          />
        </label>
      </div>
      <label className="checkbox">
        <input
          type="checkbox"
          checked={form.privacy_mode}
          onChange={(e) => setForm((f) => ({ ...f, privacy_mode: e.target.checked }))}
        />
        Privacy mode (no previews; events continue)
      </label>
      <p className="form-error" role="alert">
        {save.isError
          ? errorMessage(save.error)
          : save.isSuccess
            ? 'Saved and pushed to the phone.'
            : ''}
      </p>
      <div className="dialog-actions">
        <button type="submit" className="btn btn-primary" disabled={save.isPending}>
          Save settings
        </button>
      </div>
    </form>
  )
}
