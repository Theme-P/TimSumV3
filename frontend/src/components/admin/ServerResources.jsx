import React, { useState, useEffect, useCallback } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import Icon from '../ui/Icon';

const ServerResources = () => {
  const { token } = useAuth();
  const [resources, setResources] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const fetchResources = useCallback(async () => {
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
  }, [token]);

  useEffect(() => {
    fetchResources();
    const interval = setInterval(fetchResources, 30000);
    return () => clearInterval(interval);
  }, [fetchResources]);

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
          <Icon name={icon} />
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
          <span className="icon-label"><Icon name="refresh" className={loading ? 'ui-icon-spin' : ''} /> รีเฟรช</span>
        </button>

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
              icon="cpu"
            />
            <CircleGauge
              percent={resources.memory.percent}
              label="RAM"
              icon="server"
              subText={`${resources.memory.used_gb} / ${resources.memory.total_gb} GB`}
            />
            <CircleGauge
              percent={resources.disk.percent}
              label="Disk"
              icon="hard-drive"
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
