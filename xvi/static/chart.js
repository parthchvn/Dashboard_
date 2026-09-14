/* Local Canvas renderer. Carry-forward is visual only, never an observation. */
(() => {
  'use strict';
  const clamp = (v,lo,hi) => Math.max(lo,Math.min(hi,v));
  const price = v => Number.isFinite(v) ? `${(v*100).toFixed(1)}¢` : '—';
  const escape = v => String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const label = (t,span) => new Intl.DateTimeFormat('en-US',{
    timeZone:'UTC',...(span<172800 ? {hour:'2-digit',minute:'2-digit',hour12:false} : {month:'short',day:'numeric'})
  }).format(new Date(t*1000));

  function carryPieces(point,nextTime,staleAfter) {
    const freshEnd=Math.min(nextTime,point.observedAt+staleAfter);
    const staleEnd=Math.min(nextTime,point.observedAt+4*staleAfter);
    const pieces=[];
    if(freshEnd>point.time) pieces.push({start:point.time,end:freshEnd,stale:false});
    const fadedStart=Math.max(point.time,freshEnd);
    if(staleEnd>fadedStart) pieces.push({start:fadedStart,end:staleEnd,stale:true});
    return pieces;
  }
  function toPoints(series) {
    return series.rows.map(row=>({
      time:series.level==='raw'?row.timestamp:Math.min(row.bin_end,series.end),
      observedAt:series.level==='raw'?row.timestamp:row.last_trade_ts,
      value:series.level==='raw'?row.yes_price:row.close,
      low:series.level==='raw'?row.yes_price:row.low,
      high:series.level==='raw'?row.yes_price:row.high,
      volume:series.level==='raw'?row.usd_amount:row.notional,row
    }));
  }

  class MarketChart {
    constructor(canvas,{tooltip=null,navigator=null,onRange=null,onReset=null,compact=false}={}) {
      this.canvas=canvas;this.tooltip=tooltip;this.navigator=navigator;
      this.onRange=onRange;this.onReset=onReset;this.compact=compact;
      this.staleAfter=300;this.showRange=true;this.showDots=true;this.reviews=[];
      this.points=[];this.listeners=[];this.drag=null;this.hover=null;
      this.observer=new ResizeObserver(()=>this.draw());this.observer.observe(canvas);
      if(navigator)this.observer.observe(navigator);
      if(onRange)this.bind();
    }
    listen(target,event,fn,options) {
      target.addEventListener(event,fn,options);
      this.listeners.push(()=>target.removeEventListener(event,fn,options));
    }
    destroy() {this.observer.disconnect();this.listeners.forEach(remove=>remove());clearTimeout(this.wheelTimer);}
    set(series,{overview=false}={}) {
      this.series=series;this.points=toPoints(series);
      if(overview||!this.overview)this.overview={points:this.points,start:series.start,end:series.end};
      this.hover=null;if(this.tooltip)this.tooltip.hidden=true;this.draw();
    }
    theme() {
      const style=getComputedStyle(document.documentElement);
      const get=key=>style.getPropertyValue(key).trim();
      return {text:get('--text'),muted:get('--muted'),grid:get('--grid'),accent:get('--accent'),surface:get('--surface'),soft:get('--accent-soft')};
    }
    context(canvas) {
      const box=canvas.getBoundingClientRect(),dpr=window.devicePixelRatio||1;
      canvas.width=Math.max(1,Math.round(box.width*dpr));canvas.height=Math.max(1,Math.round(box.height*dpr));
      const ctx=canvas.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);
      return {ctx,width:box.width,height:box.height};
    }
    draw() {
      if(!this.series)return;
      const {ctx,width,height}=this.context(this.canvas),color=this.theme();
      if(!width||!height)return;
      const left=this.compact?28:40,right=this.compact?12:48,top=18;
      const bottom=this.compact?height-27:height-88;
      const start=this.series.start,end=this.series.end,span=Math.max(1,end-start);
      const x=t=>left+(t-start)/span*(width-left-right),y=p=>bottom-p*(bottom-top);
      this.geometry={left,right:width-right,top,bottom,start,end,width,height,x,y};
      ctx.clearRect(0,0,width,height);ctx.font='9px -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif';
      ctx.fillStyle=color.muted;ctx.textBaseline='middle';
      for(let n=0;n<=5;n++) {
        const yy=y(n/5);ctx.strokeStyle=color.grid;ctx.lineWidth=1;ctx.setLineDash([3,4]);
        ctx.beginPath();ctx.moveTo(left,yy);ctx.lineTo(width-right,yy);ctx.stroke();
        ctx.textAlign='right';ctx.fillText(`${n*20}${this.compact?'':'¢'}`,left-9,yy);
      }
      ctx.setLineDash([]);
      for(let n=0;n<=4;n++) {
        const t=start+span*n/4;ctx.textAlign=n===0?'left':n===4?'right':'center';
        ctx.fillText(label(t,span),x(t),this.compact?height-10:height-12);
      }
      ctx.save();ctx.beginPath();ctx.rect(left,top,width-left-right,bottom-top);ctx.clip();
      if(this.showRange&&this.series.level!=='raw') {
        ctx.strokeStyle=color.accent;ctx.globalAlpha=.2;
        ctx.lineWidth=Math.max(1,Math.min(4,(width-left-right)/Math.max(this.points.length,1)*.42));
        ctx.beginPath();
        for(const p of this.points){ctx.moveTo(x(p.time),y(p.low));ctx.lineTo(x(p.time),y(p.high));}
        ctx.stroke();ctx.globalAlpha=1;
      }
      let points=this.points;
      const previous=this.series.previous_observation;
      if(previous)points=[{time:start,observedAt:previous.timestamp,value:previous.yes_price},...points];
      ctx.strokeStyle=color.accent;ctx.lineWidth=this.compact?1.3:1.65;
      for(let i=0;i<points.length;i++) {
        const p=points[i],next=points[i+1],stop=next?next.time:end;
        for(const piece of carryPieces(p,stop,this.staleAfter)) {
          ctx.setLineDash(piece.stale?[3,4]:[]);ctx.globalAlpha=piece.stale?.3:.95;
          ctx.beginPath();ctx.moveTo(x(piece.start),y(p.value));ctx.lineTo(x(piece.end),y(p.value));ctx.stroke();
        }
        if(next&&next.time<=p.observedAt+4*this.staleAfter) {
          ctx.globalAlpha=next.time-p.observedAt>this.staleAfter?.3:.95;ctx.setLineDash([]);
          ctx.beginPath();ctx.moveTo(x(next.time),y(p.value));ctx.lineTo(x(next.time),y(next.value));ctx.stroke();
        }
      }
      ctx.globalAlpha=1;ctx.setLineDash([]);ctx.fillStyle=color.accent;
      for(const p of this.points) {
        if(this.series.level==='raw'&&!this.showDots)continue;
        ctx.globalAlpha=this.series.level==='raw'?.6:.8;
        ctx.beginPath();ctx.arc(x(p.time),y(p.value),this.compact?1.4:this.series.level==='raw'?2:1.5,0,Math.PI*2);ctx.fill();
      }
      ctx.globalAlpha=1;
      for(const review of this.reviews) {
        if(review.timestamp<start||review.timestamp>=end||!Number.isFinite(review.price))continue;
        ctx.fillStyle='#be8b42';const xx=x(review.timestamp),yy=y(review.price);
        ctx.beginPath();ctx.moveTo(xx,yy-6);ctx.lineTo(xx+5,yy);ctx.lineTo(xx,yy+6);ctx.lineTo(xx-5,yy);ctx.closePath();ctx.fill();
      }
      ctx.restore();
      const last=this.points.at(-1);
      if(last&&!this.compact) {
        ctx.fillStyle=color.accent;ctx.beginPath();ctx.arc(x(last.time),y(last.value),3.6,0,Math.PI*2);ctx.fill();
        ctx.strokeStyle=color.surface;ctx.lineWidth=1.5;ctx.stroke();
        const yy=clamp(y(last.value)-9,top-8,bottom-9);
        ctx.fillStyle=color.soft;ctx.beginPath();ctx.roundRect(width-right+5,yy,right-8,18,4);ctx.fill();
        ctx.fillStyle=color.accent;ctx.textAlign='center';ctx.fillText(price(last.value),width-right+(right+2)/2,yy+9);
      }
      if(!this.compact) {
        const baseline=height-32,maxVol=Math.max(...this.points.map(p=>p.volume),1);
        ctx.fillStyle=color.muted;ctx.font='8px -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif';
        ctx.textAlign='left';ctx.fillText('RECORDED NOTIONAL',left,bottom+19);
        ctx.fillStyle=color.accent;ctx.globalAlpha=.19;
        const barWidth=Math.max(1,Math.min(12,(width-left-right)/Math.max(this.points.length,1)*.6));
        for(const p of this.points){const h=Math.max(1,p.volume/maxVol*37);ctx.fillRect(x(p.time)-barWidth/2,baseline-h,barWidth,h);}
        ctx.globalAlpha=1;
      }
      if(this.drag) {
        ctx.fillStyle=color.soft;ctx.globalAlpha=.75;
        ctx.fillRect(Math.min(this.drag.from,this.drag.to),top,Math.abs(this.drag.to-this.drag.from),bottom-top);ctx.globalAlpha=1;
      }
      if(this.hover!==null&&!this.drag) {
        ctx.strokeStyle=color.muted;ctx.globalAlpha=.5;ctx.setLineDash([3,3]);
        ctx.beginPath();ctx.moveTo(this.hover,top);ctx.lineTo(this.hover,bottom);ctx.stroke();ctx.globalAlpha=1;ctx.setLineDash([]);
      }
      this.drawNavigator();
    }
    drawNavigator() {
      if(!this.navigator||!this.overview)return;
      const {ctx,width,height}=this.context(this.navigator),color=this.theme(),all=this.overview;
      if(!width)return;
      const x=t=>3+(t-all.start)/Math.max(1,all.end-all.start)*(width-6);
      ctx.fillStyle=color.grid;ctx.beginPath();ctx.roundRect(0,0,width,height,5);ctx.fill();
      ctx.strokeStyle=color.muted;ctx.globalAlpha=.3;ctx.lineWidth=1;ctx.beginPath();
      for(const p of all.points){ctx.moveTo(x(p.time),height-5-p.low*(height-10));ctx.lineTo(x(p.time),height-5-p.high*(height-10));}
      ctx.stroke();ctx.globalAlpha=1;
      const a=clamp(x(this.series.start),0,width),b=clamp(x(this.series.end),0,width);
      ctx.strokeStyle=color.accent;ctx.lineWidth=1;ctx.strokeRect(a+.5,.5,Math.max(1,b-a-1),height-1);
      ctx.fillStyle=color.accent;ctx.globalAlpha=.07;ctx.fillRect(a,0,b-a,height);ctx.globalAlpha=1;
      for(const xx of [a,b]){ctx.fillStyle=color.accent;ctx.fillRect(clamp(xx-1,0,width-2),height/2-6,2,12);}
    }
    bind() {
      const c=this.canvas;
      const position=e=>e.clientX-c.getBoundingClientRect().left;
      const timeAt=x=>this.geometry.start+(clamp(x,this.geometry.left,this.geometry.right)-this.geometry.left)/(this.geometry.right-this.geometry.left)*(this.geometry.end-this.geometry.start);
      this.listen(c,'pointerdown',e=>{
        if(e.button!==0||!this.geometry)return;c.setPointerCapture(e.pointerId);
        this.drag={from:clamp(position(e),this.geometry.left,this.geometry.right),to:clamp(position(e),this.geometry.left,this.geometry.right)};
      });
      this.listen(c,'pointermove',e=>{
        if(!this.geometry)return;const xx=clamp(position(e),this.geometry.left,this.geometry.right);
        if(this.drag){this.drag.to=xx;this.draw();return;}
        this.hover=xx;this.draw();this.showTooltip(timeAt(xx),xx,e.clientY-c.getBoundingClientRect().top);
      });
      this.listen(c,'pointerup',()=>{
        if(!this.drag)return;const {from,to}=this.drag;this.drag=null;this.draw();
        if(Math.abs(from-to)>6)this.onRange(Math.floor(timeAt(Math.min(from,to))),Math.ceil(timeAt(Math.max(from,to))));
      });
      this.listen(c,'pointercancel',()=>{this.drag=null;this.draw();});
      this.listen(c,'pointerleave',()=>{if(!this.drag){this.hover=null;if(this.tooltip)this.tooltip.hidden=true;this.draw();}});
      this.listen(c,'dblclick',()=>this.onReset?.());
      this.listen(c,'wheel',e=>{
        if(!this.geometry)return;e.preventDefault();
        const g=this.geometry,all=this.overview,anchor=timeAt(position(e)),span=g.end-g.start;
        const newSpan=clamp(span*Math.exp(clamp(e.deltaY,-200,200)*.0025),2,Math.max(2,all.end-all.start));
        const ratio=(anchor-g.start)/span;let lo=anchor-newSpan*ratio,hi=lo+newSpan;
        if(lo<all.start){lo=all.start;hi=lo+newSpan;}if(hi>all.end){hi=all.end;lo=hi-newSpan;}
        clearTimeout(this.wheelTimer);this.wheelTimer=setTimeout(()=>this.onRange(Math.max(0,Math.floor(lo)),Math.ceil(hi)),100);
      },{passive:false});
      this.listen(c,'keydown',e=>{if(e.key==='Escape'||e.key==='Home'){e.preventDefault();this.onReset?.();}});
      if(this.navigator) {
        let begin=null;const nx=e=>clamp(e.clientX-this.navigator.getBoundingClientRect().left,0,this.navigator.getBoundingClientRect().width);
        this.listen(this.navigator,'pointerdown',e=>{begin=nx(e);this.navigator.setPointerCapture(e.pointerId);});
        this.listen(this.navigator,'pointerup',e=>{
          if(begin===null||!this.overview)return;const finish=nx(e),first=begin;begin=null;if(Math.abs(finish-first)<6)return;
          const all=this.overview,w=this.navigator.getBoundingClientRect().width;
          this.onRange(Math.floor(all.start+Math.min(first,finish)/w*(all.end-all.start)),Math.ceil(all.start+Math.max(first,finish)/w*(all.end-all.start)));
        });
        this.listen(this.navigator,'pointercancel',()=>{begin=null;});
        this.listen(this.navigator,'dblclick',()=>this.onReset?.());
      }
    }
    showTooltip(at,xx,yy) {
      if(!this.tooltip||!this.points.length)return;
      let lo=0,hi=this.points.length-1,index=-1;
      while(lo<=hi){const mid=(lo+hi)>>1;if(this.points[mid].time<=at){index=mid;lo=mid+1;}else hi=mid-1;}
      if(index<0){this.tooltip.hidden=true;return;}
      const p=this.points[index],age=Math.max(0,Math.floor(at-p.observedAt)),row=p.row;
      const ageLabel=age<60?`${age}s`:age<3600?`${Math.floor(age/60)}m ${age%60}s`:`${(age/3600).toFixed(1)}h`;
      const date=new Date(p.observedAt*1000).toISOString().replace('T',' ').slice(0,19);
      this.tooltip.innerHTML=`<div>${escape(date)} UTC</div><strong>${price(p.value)}</strong><div>${this.series.level==='raw'?'Observed fill':'Observed-bin close'} · ${escape(this.series.level)}</div><div>${this.series.level==='raw'?`${escape(row.yes_direction)} outcome 1`:`Range ${price(p.low)} – ${price(p.high)}`}</div><div>Recorded cash: $${Number(p.volume).toLocaleString('en-US',{maximumFractionDigits:2})}</div><div class="tooltip-note">${age>this.staleAfter?'Stale observation. ':''}Last fill ${ageLabel} before cursor.${row.partial?' Partial boundary bin.':''}</div>`;
      this.tooltip.hidden=false;const width=this.canvas.getBoundingClientRect().width,tw=this.tooltip.offsetWidth;
      this.tooltip.style.left=`${clamp(xx+15,5,width-tw-5)}px`;
      this.tooltip.style.top=`${clamp(yy-70,5,this.canvas.getBoundingClientRect().height-this.tooltip.offsetHeight-5)}px`;
    }
  }
  if(typeof window!=='undefined'){window.MarketChart=MarketChart;window.xviChartPoints=toPoints;}
  if(typeof module!=='undefined')module.exports={carryPieces,toPoints,clamp};
})();
