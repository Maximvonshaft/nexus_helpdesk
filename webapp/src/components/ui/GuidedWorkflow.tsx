import { ReactNode } from 'react'

export interface GuidedWorkflowStep {
  title: string
  description: string
  status?: 'todo' | 'active' | 'done'
  meta?: ReactNode
}

export function GuidedWorkflow({ steps }: { steps: GuidedWorkflowStep[] }) {
  return (
    <ol className="guided-workflow" aria-label="处理步骤">
      {steps.map((step, index) => (
        <li key={`${step.title}-${index}`} data-status={step.status ?? 'todo'}>
          <span className="guided-workflow-index">{index + 1}</span>
          <div>
            <strong>{step.title}</strong>
            <span>{step.description}</span>
            {step.meta ? <div className="guided-workflow-meta">{step.meta}</div> : null}
          </div>
        </li>
      ))}
    </ol>
  )
}
