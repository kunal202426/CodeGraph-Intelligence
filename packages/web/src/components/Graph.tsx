import { useEffect, useRef, useState } from 'react'
import * as d3 from 'd3'
import { fetchModuleGraph, type GraphData, type GraphNode } from '../api'

interface SimNode extends GraphNode, d3.SimulationNodeDatum {}
type SimLink = d3.SimulationLinkDatum<SimNode> & { type: string }

function basename(path: string): string {
  const i = path.lastIndexOf('/')
  return i >= 0 ? path.slice(i + 1) : path
}

function makeDrag(sim: d3.Simulation<SimNode, undefined>) {
  return d3
    .drag<SVGCircleElement, SimNode>()
    .on('start', (event, d) => {
      if (!event.active) sim.alphaTarget(0.3).restart()
      d.fx = d.x
      d.fy = d.y
    })
    .on('drag', (event, d) => {
      d.fx = event.x
      d.fy = event.y
    })
    .on('end', (event, d) => {
      if (!event.active) sim.alphaTarget(0)
      d.fx = null
      d.fy = null
    })
}

export default function Graph({
  onSelect,
  selectedId,
}: {
  onSelect?: (id: string) => void
  selectedId?: string | null
}) {
  const svgRef = useRef<SVGSVGElement | null>(null)
  // Keep the latest callback in a ref so the simulation isn't rebuilt when the
  // parent passes a new inline function each render.
  const onSelectRef = useRef(onSelect)
  useEffect(() => {
    onSelectRef.current = onSelect
  }, [onSelect])

  const [data, setData] = useState<GraphData | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetchModuleGraph()
      .then(setData)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
  }, [])

  useEffect(() => {
    if (!data || !svgRef.current) return
    const svgEl = svgRef.current
    const svg = d3.select(svgEl)
    svg.selectAll('*').remove()
    const { width, height } = svgEl.getBoundingClientRect()

    const nodes: SimNode[] = data.nodes.map((n) => ({ ...n }))
    const links: SimLink[] = data.edges.map((e) => ({
      source: e.source,
      target: e.target,
      type: e.type,
    }))

    const container = svg.append('g')
    svg.call(
      d3
        .zoom<SVGSVGElement, unknown>()
        .scaleExtent([0.1, 4])
        .on('zoom', (event) => container.attr('transform', event.transform.toString())),
    )

    const simulation = d3
      .forceSimulation<SimNode>(nodes)
      .force('charge', d3.forceManyBody().strength(-200))
      .force(
        'link',
        d3
          .forceLink<SimNode, SimLink>(links)
          .id((d) => d.id)
          .distance(80),
      )
      .force('center', d3.forceCenter(width / 2, height / 2))

    const link = container
      .append('g')
      .attr('stroke', '#3f3f46')
      .attr('stroke-width', 1)
      .selectAll<SVGLineElement, SimLink>('line')
      .data(links)
      .join('line')

    const node = container
      .append('g')
      .selectAll<SVGCircleElement, SimNode>('circle')
      .data(nodes)
      .join('circle')
      .attr('r', 7)
      .attr('fill', '#a78bfa')
      .attr('cursor', 'pointer')
      .on('click', (_event, d) => onSelectRef.current?.(d.id))
      .call(makeDrag(simulation))

    const label = container
      .append('g')
      .selectAll<SVGTextElement, SimNode>('text')
      .data(nodes)
      .join('text')
      .text((d) => basename(d.label))
      .attr('font-size', 10)
      .attr('fill', '#a1a1aa')
      .attr('dx', 10)
      .attr('dy', 3)

    simulation.on('tick', () => {
      link
        .attr('x1', (d) => (d.source as SimNode).x ?? 0)
        .attr('y1', (d) => (d.source as SimNode).y ?? 0)
        .attr('x2', (d) => (d.target as SimNode).x ?? 0)
        .attr('y2', (d) => (d.target as SimNode).y ?? 0)
      node.attr('cx', (d) => d.x ?? 0).attr('cy', (d) => d.y ?? 0)
      label.attr('x', (d) => d.x ?? 0).attr('y', (d) => d.y ?? 0)
    })

    return () => {
      simulation.stop()
    }
  }, [data])

  // Highlight the selected node without rebuilding the simulation.
  useEffect(() => {
    if (!svgRef.current) return
    d3.select(svgRef.current)
      .selectAll<SVGCircleElement, SimNode>('circle')
      .attr('stroke', (d) => (d.id === selectedId ? '#f4f4f5' : 'none'))
      .attr('stroke-width', (d) => (d.id === selectedId ? 3 : 0))
      .attr('r', (d) => (d.id === selectedId ? 9 : 7))
  }, [selectedId, data])

  if (error) {
    return (
      <div className="grid h-full place-items-center text-sm text-red-400">
        Failed to load graph: {error}
      </div>
    )
  }
  if (data && data.nodes.length === 0) {
    return (
      <div className="grid h-full place-items-center text-sm text-zinc-600">
        No modules indexed yet.
      </div>
    )
  }
  return <svg ref={svgRef} className="h-full w-full" />
}
