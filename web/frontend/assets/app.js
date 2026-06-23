(function(){
  var app = document.getElementById("app");
  var issuePathRe = /\/issues\/(\d{4}-\d{2}-\d{2})\.html$/;
  var isIssuePage = issuePathRe.test(window.location.pathname);
  var assetPrefix = isIssuePage ? "../" : "";

  function dataUrl(path){ return assetPrefix + path; }

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

  function sourcesLine(article){
    var p = el("p", "source");
    var names = (article.sources || []).map(function(source){ return source.name; }).join("、");
    p.appendChild(document.createTextNode("综合 " + names + " · "));
    if(article.sources && article.sources[0]) p.appendChild(link("ul", article.sources[0].url, "原文 ↗"));
    return p;
  }

  function renderHero(article){
    var hero = el("article", "hero");
    hero.id = "hl1";
    var kicker = el("div", "kicker", article.kicker || "");
    kicker.appendChild(document.createTextNode("　"));
    kicker.appendChild(el("span", "no", "／ 头版头条"));
    append(hero, kicker, el("h2", "", article.title_zh), factSummary(article.summary_zh), readDetails(article), impactBlock(article.ai_impact), sourcesLine(article));
    return hero;
  }

  function renderSecondary(article, index){
    var card = el("article", "sec");
    card.id = "hl" + index;
    var kicker = el("div", "kicker", article.kicker || "");
    kicker.appendChild(document.createTextNode("　"));
    kicker.appendChild(el("span", "no", "／ " + String(index).padStart(2, "0")));
    append(card, kicker, el("h3", "t", article.title_zh), factSummary(article.summary_zh), readDetails(article), impactBlock(article.ai_impact), sourcesLine(article));
    return card;
  }

  function renderBrief(article, number){
    var item = el("div", "brief");
    var title = el("h3");
    append(title, el("span", "bno", String(number).padStart(2, "0")));
    title.appendChild(document.createTextNode(article.title_zh || ""));
    var source = el("p", "source");
    source.appendChild(document.createTextNode((article.sources || []).map(function(s){return s.name;}).join("、") + " · "));
    if(article.sources && article.sources[0]) source.appendChild(link("ul", article.sources[0].url, "原文↗"));
    append(item, title, el("p", "", article.summary_zh), source);
    return item;
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
      window.location.href = assetPrefix + "issues/" + select.value + ".html";
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
      headlines.appendChild(renderHero(issue.headlines[0]));
      if(issue.headlines.length > 1){
        var secondary = el("div", "secondary");
        issue.headlines.slice(1).forEach(function(article, index){
          secondary.appendChild(renderSecondary(article, index + 2));
        });
        headlines.appendChild(secondary);
      }
    }

    var briefs = el("section");
    briefs.id = "briefs";
    append(briefs, sectionLabel("速览", "快扫 · " + issue.briefs.length + " 条"));
    var briefList = el("div", "brief-list");
    issue.briefs.forEach(function(article, index){
      briefList.appendChild(renderBrief(article, issue.headlines.length + index + 1));
    });
    briefs.appendChild(briefList);

    append(wrap, masthead, nav, headlines, briefs);
    fragment.appendChild(wrap);
    app.replaceChildren(fragment);
    activateJump();
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
