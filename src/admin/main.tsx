import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import '../styles.css';
import AdminRoot from './AdminRoot';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AdminRoot />
  </StrictMode>
);
