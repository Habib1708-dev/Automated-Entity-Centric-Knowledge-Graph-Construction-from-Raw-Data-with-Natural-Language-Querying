// The load dialog (R222b): it asks first, naming what the load clears, and Cancel loads nothing.
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { LoadDialog } from '../components/LoadDialog'

beforeAll(() => {
  // jsdom has no modal dialogs: opening one only needs to mark it open here
  HTMLDialogElement.prototype.showModal = function showModal(this: HTMLDialogElement) {
    this.open = true
  }
})

afterEach(cleanup)

describe('LoadDialog', () => {
  it('names the graph it would clear and loads nothing on Cancel', () => {
    const close = vi.fn()
    const busy = vi.fn()
    const dataset = { id: 'ntsb', name: 'NTSB accidents', preset: 'ntsb', loaded: false }
    render(<LoadDialog dataset={dataset} holding="Lighthouses" onClose={close} onLoaded={() => undefined} onBusy={busy} />)
    expect(screen.getByText(/clears the graph Neo4j holds now, Lighthouses,/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(close).toHaveBeenCalled()
    expect(busy).not.toHaveBeenCalled()
  })
})
