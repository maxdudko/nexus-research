import type { Metadata } from "next";
import Link from "next/link";

import { ThemeToggle } from "@/components/theme-toggle/theme-toggle";
import "./landing.css";

export const metadata: Metadata = {
  title: "Nexus Research — From questions to inspectable reports",
  description:
    "Explore a question with AI-powered web research and optional PDF evidence. Follow the process, inspect source references, and revisit your saved reports.",
};

const steps = [
  { number: "01", title: "Start with a good question.", description: "Ask what you want to understand. Add a text PDF when you have material that should inform the research." },
  { number: "02", title: "Follow the investigation.", description: "Nexus plans the question, searches the web, gathers relevant passages, and assesses coverage. Watch each stage or stop the request." },
  { number: "03", title: "Read. Inspect. Revisit.", description: "Get a structured synthesis with source references and limitations. Return to the saved report from your account’s history." },
];

const capabilities = [
  { number: "01", title: "The web, with your context.", description: "Investigate a topic through public web pages. Attach a text PDF to bring your own material into the same research attempt.", detail: "For exploring a topic or comparing approaches" },
  { number: "02", title: "A process you can follow.", description: "See the research stages as they happen, from planning to synthesis. You can stop an active request when you need to.", detail: "Follow the stages and stop when you need to" },
  { number: "03", title: "Sources beside the findings.", description: "Follow web references and see PDF page labels alongside report sections. Read the limitations before deciding what to take forward.", detail: "Source references support your own review" },
  { number: "04", title: "A place to pick things up.", description: "Your account keeps your research history and saved reports together. Reopen a past investigation without starting it again.", detail: "Your research, scoped to your account" },
];

function Arrow() {
  return <span aria-hidden="true">↗</span>;
}

export default function LandingPage() {
  return (
    <div className="nexus-landing">
      <a href="#main" className="nexus-skip">Skip to content</a>
      <header className="nexus-header">
        <Link href="/" className="nexus-brand" aria-label="Nexus Research home">
          <span className="nexus-mark" aria-hidden="true">n<span>↗</span></span>
          Nexus Research
        </Link>
        <nav aria-label="Main navigation" className="nexus-navigation">
          <a href="#how-it-works">How it works</a>
          <a href="#vision">Vision</a>
        </nav>
        <div className="nexus-headerActions">
          <ThemeToggle />
          <Link href="/login" className="nexus-signIn">Sign in</Link>
          <Link href="/register" className="btn btn-primary">Create an account</Link>
        </div>
      </header>

      <main id="main">
        <section className="nexus-hero" aria-labelledby="hero-title">
          <div className="nexus-heroCopy">
            <p className="nexus-eyebrow"><span className="nexus-dot" /> A workspace for deeper questions</p>
            <h1 id="hero-title">From a question<br />{" "}to a report you<br />{" "}can <span>inspect.</span></h1>
            <p className="nexus-lede">Explore the web. Bring a PDF. Follow the research into structured findings, source references, and the limitations that matter.</p>
            <div className="nexus-actions">
              <Link href="/register" className="btn btn-primary">Create an account <Arrow /></Link>
              <a href="#how-it-works" className="nexus-textAction">See how it works <span aria-hidden="true">↓</span></a>
            </div>
            <p className="nexus-heroNote">AI-powered research. Room for your judgment.</p>
          </div>

          <div className="nexus-previewWrap">
            <div className="nexus-previewCaption"><span>The research, made tangible</span><span>Illustrative example</span></div>
            <article className="nexus-preview" aria-labelledby="example-title">
              <div className="nexus-previewTop"><span className="nexus-documentIcon" aria-hidden="true">≡</span><span>RESEARCH NOTE / 001</span></div>
              <div className="nexus-question"><span className="nexus-smallLabel">The question</span><p>What should a small team consider when choosing a knowledge base?</p></div>
              <div className="nexus-report">
                <p className="nexus-smallLabel">A structured synthesis</p>
                <h2 id="example-title">Choosing a knowledge base</h2>
                <p className="nexus-reportSummary">An illustrative report would organize the trade-offs around how a team writes, finds, and maintains information.</p>
                <div className="nexus-finding"><span className="nexus-findingNumber">01</span><div><h3>Start with the way your team works</h3><p>Compare authoring, search, and maintenance needs before narrowing the options.</p><span className="nexus-sourceLabel">Web source · example reference</span></div></div>
                <div className="nexus-finding"><span className="nexus-findingNumber">02</span><div><h3>Bring your requirements into the picture</h3><p>Use your own material to give the investigation context.</p><span className="nexus-sourceLabel">PDF · page 2 · example reference</span></div></div>
                <div className="nexus-previewLimit"><span className="nexus-smallLabel">Limitations</span><p>This preview shows the report format. It is not a research result or a recommendation.</p></div>
              </div>
              <div className="nexus-previewFoot"><span aria-hidden="true">↳</span> A starting point for understanding. Yours to examine.</div>
            </article>
          </div>
        </section>

        <section id="how-it-works" className="nexus-workflow" aria-labelledby="workflow-title">
          <div className="nexus-sectionHeading"><p className="nexus-eyebrow">The process</p><h2 id="workflow-title">A question in.<br />{" "}A clearer picture out.</h2><p>One connected workflow, from the first question to the sources behind the synthesis.</p></div>
          <ol className="nexus-steps">{steps.map((step) => <li key={step.number}><span className="nexus-stepNumber">{step.number}</span><h3>{step.title}</h3><p>{step.description}</p></li>)}</ol>
          <p className="nexus-pipeline">Planning <span aria-hidden="true">→</span> Discovery <span aria-hidden="true">→</span> Evidence gathering <span aria-hidden="true">→</span> Coverage assessment <span aria-hidden="true">→</span> Synthesis</p>
        </section>

        <section className="nexus-capabilities" aria-labelledby="capabilities-title">
          <div className="nexus-sectionHeading"><p className="nexus-eyebrow">Available in the current build</p><h2 id="capabilities-title">Built around the work<br />{" "}of understanding.</h2></div>
          <div className="nexus-capabilityGrid">{capabilities.map((item) => <article key={item.number}><span className="nexus-smallLabel">{item.number} /</span><h3>{item.title}</h3><p>{item.description}</p><p className="nexus-capabilityDetail">{item.detail}</p></article>)}</div>
        </section>

        <section id="vision" className="nexus-vision" aria-labelledby="vision-title">
          <p className="nexus-eyebrow">Where we’re headed</p>
          <div><h2 id="vision-title">Research should leave you<br />{" "}with more understanding.<br />{" "}<span>And a path back to the sources.</span></h2><p>Our vision is a research workspace that makes an investigation easier to follow, its conclusions easier to examine, and its findings easier to return to.</p><p>We’re building toward that through a clear process, visible source references, and uncertainty that stays in view. The goal is to help you develop your own judgment.</p><span className="nexus-visionNote">An evolving research application. A grounded product vision.</span></div>
        </section>

        <section className="nexus-expectations" aria-labelledby="expectations-title">
          <div><p className="nexus-eyebrow">Before you begin</p><h2 id="expectations-title">Keep your judgment<br />{" "}in the loop.</h2><p>Nexus is a research aid. Here’s what to expect from the current experience.</p></div>
          <dl><div><dt>References are a place to look closer.</dt><dd>A citation identifies retrieved material. It does not guarantee that a conclusion is correct. Reports may be partial or have insufficient evidence.</dd></div><div><dt>Bring a PDF with selectable text.</dt><dd>Scanned PDFs are not supported. Original files are not retained; PDF references show page labels rather than links to the file.</dd></div><div><dt>Keep the research page open.</dt><dd>Research runs while its request is active. Leaving the page can interrupt it; it does not continue in the background or automatically resume.</dd></div></dl>
        </section>

        <section className="nexus-closing" aria-labelledby="closing-title"><p className="nexus-eyebrow">Start with what you want to understand</p><h2 id="closing-title">Make room for<br />{" "}your next question.</h2><div className="nexus-actions"><Link href="/register" className="btn btn-primary">Create an account <Arrow /></Link><Link href="/login" className="nexus-textAction">Sign in <Arrow /></Link></div></section>
      </main>
      <footer className="nexus-footer"><Link href="/" className="nexus-brand">Nexus Research</Link><p>Questions. Evidence. Perspective.</p><nav aria-label="Footer" className="nexus-footerLinks">
          <a href="https://github.com/maxdudko/nexus-research" target="_blank" rel="noopener noreferrer">GitHub ↗</a>
          <a href="#main">Back to top ↑</a>
        </nav></footer>
    </div>
  );
}
