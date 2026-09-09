export function ChartArt({ system }: { system: string }) {
  return <div className="chart-art" aria-hidden="true">
    {system === "ziwei" ? <div className="palace-art">{["巳", "午", "未", "申", "辰", "", "", "酉", "卯", "", "", "戌", "寅", "丑", "子", "亥"].map((v, i) => <span key={i}>{v}</span>)}<i>☼</i></div>
      : system === "bazi" ? <div className="pillar-art">{["年", "月", "日", "时"].map((v, i) => <div key={v}><small>{v}</small><b>{["甲", "乙", "丙", "丁"][i]}</b><span>〰</span></div>)}</div>
      : <svg viewBox="0 0 240 190"><rect x="3" y="3" width="234" height="184"/><path d="M3 3L237 187M237 3L3 187M120 3L237 95L120 187L3 95Z"/>{[[120,48],[65,28],[31,60],[57,100],[30,141],[67,175],[120,144],[177,175],[210,141],[184,100],[211,60],[179,28]].map(([x,y],i)=><text key={i} x={x} y={y}>{i+1}</text>)}</svg>}
  </div>;
}
