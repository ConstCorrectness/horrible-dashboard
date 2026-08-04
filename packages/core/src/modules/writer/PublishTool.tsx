import React, { useState } from 'react';

export const PublishTool: React.FC = () => {
  const [publishing, setPublishing] = useState(false);
  const [published, setPublished] = useState(false);
  const [platforms, setPlatforms] = useState({
    substack: true,
    medium: false,
    linkedin: true,
    devto: false
  });

  const handleToggle = (key: keyof typeof platforms) => {
    setPlatforms(prev => ({ ...prev, [key]: !prev[key] }));
  };

  const handlePublish = () => {
    setPublishing(true);
    setPublished(false);
    // Simulate network delay
    setTimeout(() => {
      setPublishing(false);
      setPublished(true);
      setTimeout(() => setPublished(false), 3000);
    }, 2500);
  };

  return (
    <div style={{ padding: '16px', display: 'flex', flexDirection: 'column', gap: '16px', color: '#e0e0e0', height: '100%', overflowY: 'auto' }}>
      <h2 style={{ fontSize: '1.2rem', margin: 0, fontWeight: 600 }}>Publishing</h2>
      
      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
        <label style={{ fontSize: '0.9rem', color: '#aaa' }}>Post Title</label>
        <input 
          type="text" 
          placeholder="Enter a captivating title..." 
          style={{ 
            background: '#2a2a2a', border: '1px solid #444', color: '#fff', 
            padding: '8px 12px', borderRadius: '4px', outline: 'none' 
          }} 
        />
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
        <label style={{ fontSize: '0.9rem', color: '#aaa' }}>Tags (comma separated)</label>
        <input 
          type="text" 
          placeholder="technology, ai, design..." 
          style={{ 
            background: '#2a2a2a', border: '1px solid #444', color: '#fff', 
            padding: '8px 12px', borderRadius: '4px', outline: 'none' 
          }} 
        />
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', marginTop: '8px' }}>
        <label style={{ fontSize: '0.9rem', color: '#aaa' }}>Platforms</label>
        
        {Object.entries(platforms).map(([key, value]) => (
          <label key={key} style={{ display: 'flex', alignItems: 'center', gap: '10px', cursor: 'pointer' }}>
            <input 
              type="checkbox" 
              checked={value}
              onChange={() => handleToggle(key as keyof typeof platforms)}
              style={{ width: '16px', height: '16px', accentColor: '#61dafb' }}
            />
            <span style={{ textTransform: 'capitalize', fontSize: '0.95rem' }}>{key}</span>
          </label>
        ))}
      </div>

      <div style={{ marginTop: 'auto', paddingTop: '16px', borderTop: '1px solid #333' }}>
        <button 
          onClick={handlePublish}
          disabled={publishing || !Object.values(platforms).some(Boolean)}
          style={{ 
            width: '100%', 
            padding: '10px', 
            background: publishing ? '#444' : published ? '#4caf50' : '#61dafb', 
            color: publishing ? '#aaa' : (published ? '#fff' : '#000'), 
            border: 'none', 
            borderRadius: '4px', 
            fontWeight: 'bold',
            cursor: publishing ? 'wait' : 'pointer',
            transition: 'all 0.2s'
          }}
        >
          {publishing ? 'Publishing...' : published ? 'Published!' : 'Publish Now'}
        </button>
      </div>
    </div>
  );
};
