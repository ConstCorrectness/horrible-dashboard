import React, { useState } from 'react';

const TEMPLATES = [
  { id: 'landing', name: 'Landing Page Hero', icon: '🚀' },
  { id: 'pricing', name: 'Pricing Table', icon: '💰' },
  { id: 'dashboard', name: 'Dashboard Layout', icon: '📊' },
  { id: 'form', name: 'Contact Form', icon: '📝' },
];

export const DesignCanvas: React.FC = () => {
  const [prompt, setPrompt] = useState('');
  const [isGenerating, setIsGenerating] = useState(false);
  const [generatedResult, setGeneratedResult] = useState<string | null>(null);
  const [selectedTemplate, setSelectedTemplate] = useState<string | null>(null);

  const handleGenerate = () => {
    if (!prompt.trim() && !selectedTemplate) return;
    setIsGenerating(true);
    setGeneratedResult(null);

    // Simulate Agent generation (e.g. Kombai-like process)
    setTimeout(() => {
      setIsGenerating(false);
      setGeneratedResult('generative-success');
    }, 3000);
  };

  const handleTemplateClick = (id: string) => {
    setSelectedTemplate(id);
    setPrompt(`Generate a modern, responsive ${id} component using Tailwind CSS...`);
  };

  return (
    <div style={{ display: 'flex', height: '100%', width: '100%', background: '#121212', color: '#e0e0e0' }}>
      
      {/* Left Sidebar: Controls & Prompt */}
      <div style={{ width: '320px', borderRight: '1px solid #333', display: 'flex', flexDirection: 'column', padding: '20px', gap: '20px', background: '#1a1a1a' }}>
        <div>
          <h2 style={{ fontSize: '1.2rem', margin: '0 0 8px 0' }}>Design Canvas</h2>
          <p style={{ fontSize: '0.85rem', color: '#aaa', margin: 0 }}>Prompt the AI to generate React UI components.</p>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          <label style={{ fontSize: '0.9rem', color: '#aaa', fontWeight: 600 }}>Describe UI</label>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="e.g. A sleek dark mode pricing table with 3 tiers..."
            style={{
              width: '100%', height: '120px', resize: 'none', background: '#222', 
              border: '1px solid #444', color: '#fff', padding: '10px', 
              borderRadius: '6px', fontFamily: 'inherit', outline: 'none'
            }}
          />
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          <label style={{ fontSize: '0.9rem', color: '#aaa', fontWeight: 600 }}>Quick Templates</label>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px' }}>
            {TEMPLATES.map(t => (
              <button
                key={t.id}
                onClick={() => handleTemplateClick(t.id)}
                style={{
                  background: selectedTemplate === t.id ? '#2c3e50' : '#2a2a2a',
                  border: `1px solid ${selectedTemplate === t.id ? '#3498db' : '#444'}`,
                  color: '#fff', padding: '10px 8px', borderRadius: '6px',
                  cursor: 'pointer', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '4px',
                  transition: 'all 0.2s'
                }}
              >
                <span style={{ fontSize: '1.2rem' }}>{t.icon}</span>
                <span style={{ fontSize: '0.75rem', textAlign: 'center' }}>{t.name}</span>
              </button>
            ))}
          </div>
        </div>

        <button
          onClick={handleGenerate}
          disabled={isGenerating || (!prompt && !selectedTemplate)}
          style={{
            marginTop: 'auto', padding: '12px', background: isGenerating ? '#444' : '#61dafb',
            color: isGenerating ? '#aaa' : '#000', border: 'none', borderRadius: '6px',
            fontWeight: 'bold', cursor: isGenerating ? 'wait' : 'pointer', transition: 'all 0.2s'
          }}
        >
          {isGenerating ? 'Generating UI...' : '✨ Generate Code'}
        </button>
      </div>

      {/* Right Canvas: Render Area */}
      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '40px', background: 'radial-gradient(circle at center, #2a2a2a 0%, #121212 100%)' }}>
        
        {!isGenerating && !generatedResult && (
          <div style={{ textAlign: 'center', color: '#666' }}>
            <div style={{ fontSize: '3rem', marginBottom: '16px' }}>🎨</div>
            <h3>Empty Canvas</h3>
            <p style={{ fontSize: '0.9rem' }}>Select a template or type a prompt to begin.</p>
          </div>
        )}

        {isGenerating && (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '16px' }}>
            <div style={{ width: '40px', height: '40px', border: '3px solid #333', borderTopColor: '#61dafb', borderRadius: '50%', animation: 'spin 1s linear infinite' }} />
            <style>{`@keyframes spin { 100% { transform: rotate(360deg); } }`}</style>
            <p style={{ color: '#aaa', fontFamily: 'monospace' }}>Compiling AI response into React/Tailwind...</p>
          </div>
        )}

        {generatedResult && !isGenerating && (
          <div style={{ width: '100%', height: '100%', background: '#fff', borderRadius: '8px', overflow: 'hidden', boxShadow: '0 10px 30px rgba(0,0,0,0.5)', display: 'flex', flexDirection: 'column' }}>
            {/* Mock Browser Bar */}
            <div style={{ background: '#f1f1f1', padding: '8px 16px', display: 'flex', gap: '6px', borderBottom: '1px solid #ddd' }}>
              <div style={{ width: '12px', height: '12px', borderRadius: '50%', background: '#ff5f56' }} />
              <div style={{ width: '12px', height: '12px', borderRadius: '50%', background: '#ffbd2e' }} />
              <div style={{ width: '12px', height: '12px', borderRadius: '50%', background: '#27c93f' }} />
            </div>
            
            {/* Mock Rendered Content */}
            <div style={{ flex: 1, padding: '40px', color: '#333', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '20px' }}>
              <h1 style={{ fontSize: '2.5rem', fontWeight: 800, margin: 0, color: '#111' }}>Next-Gen Developer Tools</h1>
              <p style={{ fontSize: '1.2rem', color: '#666', maxWidth: '600px', textAlign: 'center', margin: 0 }}>
                This is a simulated render of the generated UI component. In a real environment, this would be an iframe or a sandboxed React runner evaluating the agent's code.
              </p>
              <div style={{ display: 'flex', gap: '16px', marginTop: '20px' }}>
                <button style={{ background: '#000', color: '#fff', padding: '12px 24px', borderRadius: '8px', border: 'none', fontWeight: 600, fontSize: '1rem' }}>Get Started</button>
                <button style={{ background: '#fff', color: '#000', padding: '12px 24px', borderRadius: '8px', border: '1px solid #ddd', fontWeight: 600, fontSize: '1rem' }}>View Source</button>
              </div>
            </div>
          </div>
        )}

      </div>
    </div>
  );
};
