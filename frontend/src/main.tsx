import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './style.css'
import './styles/markdown.css'
import './styles/workspaces.css'
import './styles/detection.css'
createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>)
