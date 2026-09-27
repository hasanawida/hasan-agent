/* Touch gestures never press the remote mouse until a tap or deliberate hold. */
(function(root, factory) {
  if(typeof module === 'object' && module.exports) module.exports=factory();
  else root.DesktopTouch=factory();
})(typeof window === 'undefined' ? this : window, () => {
  'use strict';
  return class DesktopTouch {
    constructor(send, {later=setTimeout, cancel=clearTimeout, now=()=>performance.now(), changed=()=>{}}={}) {
      this.send=send;this.later=later;this.cancel=cancel;this.now=now;this.changed=changed;this.lastMove=0;
      this.fingers=new Map();this.mode='idle';this.timer=null;this.dragging=false;this.remainder=0;
    }
    clearTimer() {if(this.timer!==null)this.cancel(this.timer);this.timer=null;}
    setMode(mode) {this.mode=mode;this.changed(mode);}
    endDrag() {
      const held=this.dragging;this.dragging=false;
      if(held)this.send({action:'button',button:'left',down:false});
    }
    reset() {
      this.clearTimer();this.endDrag();this.fingers.clear();this.remainder=0;this.setMode('idle');
    }
    center() {
      const points=[...this.fingers.values()];
      return points.reduce((total,p)=>total+p.clientY,0)/points.length;
    }
    down(id, data) {
      if(this.fingers.has(id))return;
      this.fingers.set(id,data);
      if(this.fingers.size===1) {
        this.start=data;this.remainder=0;this.setMode('pending');
        this.timer=this.later(()=>{
          this.timer=null;
          if(this.mode!=='pending'||this.fingers.size!==1)return;
          this.send({action:'move',...this.start.position});
          this.dragging=true;this.lastMove=this.now();this.setMode('selecting');
          this.send({action:'button',button:'left',down:true});
        },450);
      } else {
        this.clearTimer();this.endDrag();
        if(this.fingers.size===2 && this.mode!=='blocked') {
          this.send({action:'move',...this.start.position});
          this.remainder=0;this.lastY=this.center();this.setMode('scrolling');
        } else this.setMode('blocked');
      }
    }
    move(id, data, final=false) {
      if(!this.fingers.has(id))return;
      this.fingers.set(id,data);
      if(this.mode==='pending') {
        if(Math.hypot(data.clientX-this.start.clientX,data.clientY-this.start.clientY)<10)return;
        this.clearTimer();this.lastY=this.start.clientY;
        this.send({action:'move',...this.start.position});this.setMode('scrolling');
      }
      if(this.mode==='selecting') {
        if(final||this.now()-this.lastMove>=40){this.lastMove=this.now();this.send({action:'move',...data.position});}
      }
      else if(this.mode==='scrolling') {
        const y=this.center();this.remainder+=y-this.lastY;this.lastY=y;
        const steps=Math.trunc(this.remainder/24);
        if(steps) {
          this.remainder-=steps*24;
          this.send({action:'scroll',dy:Math.max(-5,Math.min(5,steps))});
        }
      }
    }
    up(id, data) {
      if(!this.fingers.has(id))return;
      this.move(id,data,true);this.clearTimer();
      if(this.mode==='pending') {
        this.send({action:'move',...data.position});
        this.send({action:'button',button:'left',down:true});
        this.send({action:'button',button:'left',down:false});
      } else if(this.mode==='selecting') this.endDrag();
      this.fingers.delete(id);
      // Remaining fingers must lift before another gesture; never turn a scroll into a click.
      if(this.fingers.size)this.setMode('blocked');
      else {this.remainder=0;this.setMode('idle');}
    }
  };
});
