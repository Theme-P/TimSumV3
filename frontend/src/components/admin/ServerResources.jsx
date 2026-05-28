import React, { useState, useEffect } from 'react';
import { useAuth } from '../../contexts/AuthContext';

const ServerResources = () => {
  const { token, user } = useAuth();
  const [resources, setResources] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [restarting, setRestarting] = useState(false);

  const fetchResources = async () => {
    try {
      setLoading(true);
      setError('');
      const res = await fetch('/api/admin/system/resources', {
        headers: { Authorization: `Bearer ${token}` }
      });
      if (!res.ok) throw new Error('Failed to fetch resources');
      const data = await res.json();
      setResources(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchResources();
    const interval = setInterval(fetchResources, 30000);
    return () => clearInterval(interval);
  }, [token]);

  const handleRestartOllama = async () => {
    if (!window.confirm("คุณต้องการเริ่มระบบ Ollama ใหม่ใช่หรือไม่? (อาจทำให้การทำงานที่กำลังรันอยู่หยุดชะงัก)")) {
      return;
    }
    try {
      setRestarting(true);
      const res = await fetch('/api/admin/system/ollama/restart', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` }
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Failed to restart Ollama');
      alert(data.message || 'Ollama restarted successfully');
      fetchResources();
    } catch (err) {
      alert('Error restarting Ollama: ' + err.message);
    } finally {
      setRestarting(false);
    }
  };

  const circleSize = 72;

  const getColor = (percent) => {
    if (percent < 60) return '#2d8a4e';
    if (percent < 85) return '#c68a19';
    return '#c0392b';
  };

  const CircleGauge = ({ percent, label, icon, subText }) => {
    const color = getColor(percent);
    return (
      <div style={{
        display: 'flex', flexDirection: 'column', alignItems: 'center',
        background: 'var(--surface-elevated)', padding: '1rem',
        borderRadius: 12, border: '1px solid var(--border-color)',
      }}>
        <div style={{
          display: 'flex', alignItems: 'center', gap: '0.4rem',
          marginBottom: '0.5rem', color: 'var(--text-secondary)', fontSize: '0.85rem',
        }}>
          <span>{icon}</span>
          <span style={{ fontWeight: 600 }}>{label}</span>
        </div>
        <div style={{ position: 'relative', width: circleSize, height: circleSize }}>
          <svg width={circleSize} height={circleSize} viewBox="0 0 36 36">
            <path
              d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"
              fill="none" stroke="var(--border-color)" strokeWidth="3"
            />
            <path
              d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"
              fill="none" stroke={color} strokeWidth="3"
              strokeDasharray={`${percent}, 100`} strokeLinecap="round"
            />
          </svg>
          <div style={{
            position: 'absolute', inset: 0, display: 'flex',
            alignItems: 'center', justifyContent: 'center',
          }}>
            <span style={{ fontSize: '1rem', fontWeight: 700, color: 'var(--text-primary)' }}>
              {percent}%
            </span>
          </div>
        </div>
        {subText && (
          <div style={{ marginTop: '0.4rem', fontSize: '0.78rem', color: 'var(--text-muted)', textAlign: 'center' }}>
            {subText}
          </div>
        )}
      </div>
    );
  };

  if (loading && !resources) {
    return <div className="history-loading"><div className="history-spinner" /><span>กำลังโหลดข้อมูลเซิร์ฟเวอร์...</span></div>;
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.5rem', marginBottom: '1rem' }}>
        <button
          onClick={fetchResources}
          style={{
            padding: '0.4rem 0.8rem', borderRadius: 8, border: '1px solid var(--border-color)',
            background: 'var(--surface-elevated)', color: 'var(--text-secondary)',
            cursor: 'pointer', fontSize: '0.82rem', fontFamily: 'var(--font-thai)',
          }}
          title="รีเฟรชข้อมูล"
        >
          {loading ? '⏳' : '🔄'} รีเฟรช
        </button>

        {(user?.role === 'admin' || user?.role === 'superadmin') && (
          <button
            onClick={handleRestartOllama}
            disabled={restarting}
            style={{
              padding: '0.4rem 0.8rem', borderRadius: 8, border: '1px solid var(--border-color)',
              background: 'var(--surface-elevated)', color: '#7c3aed',
              cursor: restarting ? 'not-allowed' : 'pointer', fontSize: '0.82rem',
              fontFamily: 'var(--font-thai)', opacity: restarting ? 0.5 : 1,
            }}
          >
            ⚡ {restarting ? 'กำลังรีสตาร์ท...' : 'รีสตาร์ท Ollama'}
          </button>
        )}
      </div>

      {error ? (
        <div className="upload-error">{error}</div>
      ) : resources ? (
        <div>
          <div style={{
            display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)',
            gap: '0.75rem', marginBottom: '1rem',
          }}>
            <CircleGauge
              percent={resources.cpu_percent}
              label="CPU"
              icon="⚙️"
            />
            <CircleGauge
              percent={resources.memory.percent}
              label="RAM"
              icon="🧠"
              subText={`${resources.memory.used_gb} / ${resources.memory.total_gb} GB`}
            />
            <CircleGauge
              percent={resources.disk.percent}
              label="Disk"
              icon="💾"
              subText={`${resources.disk.used_gb} / ${resources.disk.total_gb} GB`}
            />
          </div>
          <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', textAlign: 'right' }}>
            อัปเดตล่าสุด: {new Date(resources.timestamp).toLocaleTimeString('th-TH')}
          </div>
        </div>
      ) : null}
    </div>
  );
};

export default ServerResources;
