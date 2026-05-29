import { useParams } from 'react-router-dom'

export function TasksPage() {
  const { projectId } = useParams()
  return <h1>Tasks for project {projectId}</h1>
}
