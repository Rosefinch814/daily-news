(function(){
  var app = document.getElementById("app");
  var issuePathRe = /\/issues\/(\d{4}-\d{2}-\d{2})(?:\.html)?$/;
  var appRoot = new URL("../", document.currentScript.src);

  function dataUrl(path){ return new URL(path, appRoot).href; }

  function loadScript(src){
    return new Promise(function(resolve, reject){
      var existing = document.querySelector('script[src="' + src + '"]');
      if(existing){ resolve(); return; }
      var script = document.createElement("script");
      script.src = src;
      script.onload = resolve;
      script.onerror = function(){ reject(new Error("无法加载 " + src)); };
      document.head.appendChild(script);
    });
  }

  async function loadJson(path, scriptPath, getter){
    try{
      var response = await fetch(dataUrl(path), {cache:"no-store"});
      if(!response.ok) throw new Error(response.status + " " + response.statusText);
      return await response.json();
    }catch(error){
      await loadScript(dataUrl(scriptPath));
      var value = getter();
      if(!value) throw error;
      return value;
    }
  }

  function getRequestedDate(manifest){
    var queryDate = new URLSearchParams(window.location.search).get("date");
    if(queryDate) return queryDate;
    var hashMatch = window.location.hash.match(/(\d{4}-\d{2}-\d{2})/);
    if(hashMatch) return hashMatch[1];
    var pathMatch = window.location.pathname.match(issuePathRe);
    if(pathMatch) return pathMatch[1];
    return manifest.latest_issue_date;
  }

  function el(tag, className, text){
    var node = document.createElement(tag);
    if(className) node.className = className;
    if(text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function link(className, href, text){
    var node = el("a", className, text);
    node.href = href || "#";
    node.target = "_blank";
    node.rel = "noopener noreferrer";
    return node;
  }

  function append(parent){
    for(var i=1;i<arguments.length;i++){
      var child = arguments[i];
      if(child) parent.appendChild(child);
    }
    return parent;
  }

  function feedbackPublicConfig(){
    return (window.DAILY_NEWS_MANIFEST && window.DAILY_NEWS_MANIFEST.public_config) || {};
  }

  function feedbackMode(){
    return feedbackPublicConfig().feedback_mode === "owner" ? "owner" : "reader";
  }

  function ownerTokenKey(){
    return ["owner", "token"].join("_");
  }

  function feedbackEnabled(){
    var config = feedbackPublicConfig();
    return Boolean(config.supabase_url && config.supabase_anon_key);
  }

  function ownerFeedbackEnabled(){
    var config = feedbackPublicConfig();
    return feedbackEnabled() && feedbackMode() === "owner" && Boolean(config[ownerTokenKey()]);
  }

  function feedbackQueueKey(){
    return "daily-news-feedback-queue";
  }

  function queuedFeedback(){
    try{
      return JSON.parse(localStorage.getItem(feedbackQueueKey()) || "[]");
    }catch(error){
      return [];
    }
  }

  function saveFeedbackQueue(queue){
    try{
      localStorage.setItem(feedbackQueueKey(), JSON.stringify(queue));
    }catch(error){}
  }

  async function postFeedback(table, payload){
    var config = feedbackPublicConfig();
    if(!config.supabase_url || !config.supabase_anon_key) throw new Error("反馈功能未配置");
    var response = await fetch(config.supabase_url.replace(/\/$/, "") + "/rest/v1/" + table, {
      method:"POST",
      headers:{
        "apikey":config.supabase_anon_key,
        "Authorization":"Bearer " + config.supabase_anon_key,
        "Content-Type":"application/json",
        "Prefer":"return=minimal"
      },
      body:JSON.stringify(payload)
    });
    if(!response.ok) throw new Error("反馈写入失败：" + response.status);
  }

  async function flushFeedbackQueue(){
    if(!feedbackEnabled()) return;
    var queue = queuedFeedback();
    if(!queue.length) return;
    var remaining = [];
    for(var i=0;i<queue.length;i++){
      try{
        var item = queue[i];
        if(!item.table && feedbackMode() !== "owner") continue;
        await postFeedback(item.table || "feedback", item.payload || item);
      }catch(error){
        remaining.push(queue[i]);
      }
    }
    saveFeedbackQueue(remaining);
  }

  function queueFeedback(table, payload){
    var queue = queuedFeedback();
    queue.push({table:table, payload:payload});
    saveFeedbackQueue(queue);
  }

  function feedbackPayload(issue, scope, options){
    options = options || {};
    var config = feedbackPublicConfig();
    var payload = {
      issue_id: issue.id,
      issue_date: issue.issue_date,
      section_slug: issue.section_slug,
      scope: scope,
      article_level: options.articleLevel || null,
      article_index: options.articleIndex || null,
      source_item_ids: options.sourceItemIds || [],
      signal: options.signal || null,
      note: options.note || null
    };
    if(config[ownerTokenKey()]) payload[ownerTokenKey()] = config[ownerTokenKey()];
    return payload;
  }

  function productFeedbackPayload(issue, note){
    return {
      issue_id: issue.id,
      issue_date: issue.issue_date,
      section_slug: issue.section_slug,
      note: note
    };
  }

  function articleFeedbackKey(issue, level, index){
    return [issue.id, level, index].join(":");
  }

  var articleFeedbackState = {};
  var articleFeedbackTimers = {};

  function setFeedbackStatus(node, text){
    if(!node) return;
    node.textContent = text || "";
    if(text){
      window.setTimeout(function(){
        if(node.textContent === text) node.textContent = "";
      }, 2600);
    }
  }

  async function submitFeedback(table, payload, statusNode, successText){
    try{
      await flushFeedbackQueue();
      await postFeedback(table, payload);
      setFeedbackStatus(statusNode, successText || "已记下");
    }catch(error){
      queueFeedback(table, payload);
      setFeedbackStatus(statusNode, "已暂存 · 联网后重试");
    }
  }

  function updateFeedbackReview(root){
    if(!root) return;
    var up = document.querySelectorAll(".fb-btn.is-on[data-signal='up']").length;
    var down = document.querySelectorAll(".fb-btn.is-on[data-signal='down']").length;
    root.textContent = "本期已标记：" + up + " 条好，" + down + " 条不好。";
  }

  function renderArticleFeedback(issue, article, options){
    if(!ownerFeedbackEnabled()) return null;
    var key = articleFeedbackKey(issue, options.level, options.index);
    articleFeedbackState[key] = articleFeedbackState[key] || {signal:null, note:""};
    var box = el("span", "feedback-inline");
    var status = el("span", "fb-status");
    var up = document.createElement("button");
    var down = document.createElement("button");
    [up, down].forEach(function(button){
      button.type = "button";
      button.className = "fb-btn";
    });
    up.dataset.signal = "up";
    down.dataset.signal = "down";
    up.setAttribute("aria-label", "标记为好");
    down.setAttribute("aria-label", "标记为不好");
    up.setAttribute("aria-pressed", "false");
    down.setAttribute("aria-pressed", "false");
    up.textContent = "👍 好";
    down.textContent = "👎 不好";

    function syncButtons(){
      var signal = articleFeedbackState[key].signal;
      up.classList.toggle("is-on", signal === "up");
      down.classList.toggle("is-on", signal === "down");
      up.setAttribute("aria-pressed", signal === "up" ? "true" : "false");
      down.setAttribute("aria-pressed", signal === "down" ? "true" : "false");
      if(options.noteRow) options.noteRow.hidden = !signal;
      updateFeedbackReview(document.querySelector(".issue-feedback-review"));
    }

    function scheduleWrite(){
      window.clearTimeout(articleFeedbackTimers[key]);
      articleFeedbackTimers[key] = window.setTimeout(function(){
        var state = articleFeedbackState[key];
        submitFeedback("feedback", feedbackPayload(issue, "article", {
          articleLevel: options.level,
          articleIndex: options.index,
          sourceItemIds: article.source_item_ids || [],
          signal: state.signal,
          note: state.note || null
        }), status, "已记下 · 调整下一期");
      }, 1000);
    }

    function handleClick(signal){
      articleFeedbackState[key].signal = articleFeedbackState[key].signal === signal ? null : signal;
      syncButtons();
      scheduleWrite();
    }

    up.addEventListener("click", function(){ handleClick("up"); });
    down.addEventListener("click", function(){ handleClick("down"); });
    append(box, up, down, status);
    return {node:box, sync:syncButtons, status:status};
  }

  function sectionLabel(cn, role){
    var box = el("div", "section-label");
    append(box, el("span", "tab"), el("span", "cn", cn), el("span", "role", role), el("span", "line"));
    return box;
  }

  function factSummary(text, className){
    var p = el("p", className || "summary");
    append(p, el("span", "tag-fact", "事实"));
    p.appendChild(document.createTextNode(text || ""));
    return p;
  }

  function readDetails(article){
    var details = document.createElement("details");
    var summary = document.createElement("summary");
    append(summary, el("span", "tw"), document.createTextNode("展开精读"));
    var body = el("div", "read-body");
    (article.read_body_zh || []).forEach(function(paragraph){
      body.appendChild(el("p", "", paragraph));
    });
    if(article.pullquote && article.pullquote.text){
      var quote = el("p", "pullquote", article.pullquote.text);
      if(article.pullquote.cite) quote.appendChild(el("cite", "", article.pullquote.cite));
      body.appendChild(quote);
    }
    append(details, summary, body);
    return details;
  }

  function impactBlock(text){
    var judgement = el("div", "judgement");
    var impact = el("div", "ai-impact");
    append(impact, el("span", "mk", "影响 · AI 分析（非原文事实）"));
    impact.appendChild(document.createTextNode(text || ""));
    judgement.appendChild(impact);
    return judgement;
  }

  function sourcesLine(article, issue, options){
    var block = el("div", "source-block");
    var p = el("p", "source");
    var names = (article.sources || []).map(function(source){ return source.name; }).join("、");
    p.appendChild(document.createTextNode("综合 " + names + " · "));
    if(article.sources && article.sources[0]) p.appendChild(link("ul", article.sources[0].url, "原文 ↗"));
    var noteRow = null;
    if(options && options.allowNote && ownerFeedbackEnabled()){
      noteRow = el("div", "feedback-note");
      noteRow.hidden = true;
      var input = document.createElement("input");
      input.type = "text";
      input.maxLength = 2000;
      input.placeholder = "补充一句";
      var button = document.createElement("button");
      button.type = "button";
      button.textContent = "记下";
      append(noteRow, input, button);
      button.addEventListener("click", function(){
        var key = articleFeedbackKey(issue, options.level, options.index);
        var state = articleFeedbackState[key] || {signal:null, note:""};
        var note = input.value.trim();
        if(!note) return;
        state.note = note;
        articleFeedbackState[key] = state;
        submitFeedback("feedback", feedbackPayload(issue, "article", {
          articleLevel: options.level,
          articleIndex: options.index,
          sourceItemIds: article.source_item_ids || [],
          signal: state.signal,
          note: note
        }), noteRow.querySelector(".fb-status"), "已记下 · 调整下一期");
      });
      input.addEventListener("keydown", function(event){
        if(event.key === "Enter"){
          event.preventDefault();
          button.click();
        }
      });
      noteRow.appendChild(el("span", "fb-status"));
    }
    if(issue && options){
      var feedback = renderArticleFeedback(issue, article, {
        level: options.level,
        index: options.index,
        noteRow: noteRow
      });
      if(feedback){
        p.appendChild(feedback.node);
        feedback.sync();
      }
    }
    append(block, p, noteRow);
    return block;
  }

  function renderHero(article, issue){
    var hero = el("article", "hero");
    hero.id = "hl1";
    var kicker = el("div", "kicker", article.kicker || "");
    kicker.appendChild(document.createTextNode("　"));
    kicker.appendChild(el("span", "no", "／ 头版头条"));
    append(
      hero,
      kicker,
      el("h2", "", article.title_zh),
      factSummary(article.summary_zh),
      readDetails(article),
      impactBlock(article.ai_impact),
      sourcesLine(article, issue, {level:"headline", index:1, allowNote:true})
    );
    return hero;
  }

  function renderSecondary(article, index, issue){
    var card = el("article", "sec");
    card.id = "hl" + index;
    var kicker = el("div", "kicker", article.kicker || "");
    kicker.appendChild(document.createTextNode("　"));
    kicker.appendChild(el("span", "no", "／ " + String(index).padStart(2, "0")));
    append(
      card,
      kicker,
      el("h3", "t", article.title_zh),
      factSummary(article.summary_zh),
      readDetails(article),
      impactBlock(article.ai_impact),
      sourcesLine(article, issue, {level:"headline", index:index, allowNote:true})
    );
    return card;
  }

  function renderBrief(article, number, issue, briefIndex){
    var item = el("div", "brief");
    var title = el("h3");
    append(title, el("span", "bno", String(number).padStart(2, "0")));
    title.appendChild(document.createTextNode(article.title_zh || ""));
    append(
      item,
      title,
      el("p", "", article.summary_zh),
      sourcesLine(article, issue, {level:"brief", index:briefIndex, allowNote:false})
    );
    return item;
  }

  function renderIssueFeedback(issue){
    if(!feedbackEnabled()) return null;
    var section = el("section", "issue-feedback");
    var isOwner = ownerFeedbackEnabled();
    append(section, sectionLabel(isOwner ? "本期反馈" : "读者反馈", isOwner ? "只影响下一期" : "留言箱"));
    var review = isOwner ? el("p", "issue-feedback-review", "本期已标记：0 条好，0 条不好。") : null;
    var textarea = document.createElement("textarea");
    textarea.maxLength = 2000;
    textarea.rows = 4;
    textarea.placeholder = isOwner ? "对这一期整体有什么想补充的？" : "对这份日报有什么想说的？";
    var button = document.createElement("button");
    button.type = "button";
    button.className = "issue-feedback-submit";
    button.textContent = isOwner ? "记下本期" : "记下";
    var status = el("span", "fb-status");
    button.addEventListener("click", function(){
      var note = textarea.value.trim();
      if(!note) return;
      if(isOwner){
        submitFeedback("feedback", feedbackPayload(issue, "issue", {note:note}), status, "已记下 · 调整下一期");
      }else{
        submitFeedback("product_feedback", productFeedbackPayload(issue, note), status, "已记下");
      }
    });
    append(
      section,
      review,
      textarea,
      append(el("div", "issue-feedback-actions"), button, status),
      el("p", "feedback-hint", isOwner ? "反馈不会改变本期内容，只会用于调整下一期。" : "你的建议我会看到。")
    );
    return section;
  }

  function renderIssuePicker(manifest, currentDate){
    var issues = manifest.issues || [];
    if(issues.length < 2) return null;
    var label = el("label", "issue-picker", "期数");
    var select = document.createElement("select");
    issues.forEach(function(item){
      var option = document.createElement("option");
      option.value = item.date;
      option.textContent = item.date_cn || item.date;
      option.selected = item.date === currentDate;
      select.appendChild(option);
    });
    select.addEventListener("change", function(){
      window.location.href = new URL("issues/" + select.value + ".html", appRoot).href;
    });
    label.appendChild(select);
    return label;
  }

  function activateJump(){
    var ids = ["headlines","briefs"];
    var links = document.querySelectorAll(".jump a");
    if(!("IntersectionObserver" in window)) return;
    var observer = new IntersectionObserver(function(entries){
      entries.forEach(function(entry){
        if(entry.isIntersecting){
          var index = ids.indexOf(entry.target.id);
          links.forEach(function(item){ item.classList.remove("active"); });
          if(links[index]) links[index].classList.add("active");
        }
      });
    }, {rootMargin:"-55px 0px -65% 0px"});
    ids.forEach(function(id){
      var section = document.getElementById(id);
      if(section) observer.observe(section);
    });
  }

  function render(issue, manifest, currentDate){
    document.title = "Tourbillion News · Technology";
    var fragment = document.createDocumentFragment();
    fragment.appendChild(el("div", "redband"));
    var wrap = el("div", "wrap");
    var masthead = el("header", "masthead");
    var brand = el("div");
    var h1 = el("h1");
    h1.appendChild(document.createTextNode("Tourbillion"));
    h1.appendChild(el("span", "tb", "."));
    h1.appendChild(document.createTextNode("News"));
    append(brand, h1, el("div", "sub", "Technology"));
    var dateBox = el("div", "dateline", issue.date_cn || issue.issue_date || "");
    append(masthead, brand, append(el("div"), dateBox, renderIssuePicker(manifest, currentDate)));
    var nav = el("nav", "jump");
    append(nav, anchor("#headlines", "头条"), anchor("#briefs", "速览"));

    var headlines = el("section");
    headlines.id = "headlines";
    append(headlines, sectionLabel("头条精读", "深读 · " + issue.headlines.length + " 条"));
    if(issue.headlines.length){
      headlines.appendChild(renderHero(issue.headlines[0], issue));
      if(issue.headlines.length > 1){
        var secondary = el("div", "secondary");
        issue.headlines.slice(1).forEach(function(article, index){
          secondary.appendChild(renderSecondary(article, index + 2, issue));
        });
        headlines.appendChild(secondary);
      }
    }

    var briefs = el("section");
    briefs.id = "briefs";
    append(briefs, sectionLabel("速览", "快扫 · " + issue.briefs.length + " 条"));
    var briefList = el("div", "brief-list");
    issue.briefs.forEach(function(article, index){
      briefList.appendChild(renderBrief(article, issue.headlines.length + index + 1, issue, index + 1));
    });
    briefs.appendChild(briefList);

    append(wrap, masthead, nav, headlines, briefs, renderIssueFeedback(issue));
    fragment.appendChild(wrap);
    app.replaceChildren(fragment);
    activateJump();
    flushFeedbackQueue();
  }

  function anchor(href, text){
    var node = el("a", "", text);
    node.href = href;
    return node;
  }

  function showError(error){
    var box = el("div", "error");
    append(box, el("p", "", "日报加载失败。"), el("p", "", String(error && error.message ? error.message : error)));
    app.replaceChildren(box);
  }

  async function start(){
    var manifest = await loadJson("data/manifest.json", "data/manifest.js", function(){ return window.DAILY_NEWS_MANIFEST; });
    window.DAILY_NEWS_MANIFEST = manifest;
    var currentDate = getRequestedDate(manifest);
    var issue = await loadJson(
      "data/issues/" + currentDate + ".json",
      "data/issues/" + currentDate + ".js",
      function(){ return window.DAILY_NEWS_ISSUES && window.DAILY_NEWS_ISSUES[currentDate]; }
    );
    render(issue, manifest, currentDate);
  }

  start().catch(showError);
})();
