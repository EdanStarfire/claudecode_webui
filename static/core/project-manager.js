/**
 * Project Manager - handles project operations and state
 */
class ProjectManager {
    constructor(webui) {
        this.webui = webui;
        this.projects = new Map(); // project_id -> ProjectData
        this.orderedProjects = []; // Maintains project order from backend
    }

    async loadProjects() {
        try {
            const response = await fetch('/api/projects');
            const data = await response.json();

            this.projects.clear();
            this.orderedProjects = [];

            for (const project of data.projects) {
                this.projects.set(project.project_id, project);
                this.orderedProjects.push(project.project_id);
            }

            Logger.info('PROJECT', `Loaded ${this.projects.size} projects`);
            return data.projects;
        } catch (error) {
            Logger.error('PROJECT', 'Failed to load projects', error);
            throw error;
        }
    }

    async createProject(name, workingDirectory, isMultiAgent = false) {
        try {
            const response = await fetch('/api/projects', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    name: name,
                    working_directory: workingDirectory,
                    is_multi_agent: isMultiAgent
                })
            });

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.detail || 'Failed to create project');
            }

            const data = await response.json();
            const project = data.project;

            this.projects.set(project.project_id, project);
            this.orderedProjects.unshift(project.project_id); // Add to top

            Logger.info('PROJECT', `Created project ${project.project_id}`, project);
            return project;
        } catch (error) {
            Logger.error('PROJECT', 'Failed to create project', error);
            throw error;
        }
    }

    async getProjectWithSessions(projectId) {
        try {
            const response = await fetch(`/api/projects/${projectId}`);
            if (!response.ok) {
                throw new Error('Project not found');
            }

            const data = await response.json();
            return data;
        } catch (error) {
            Logger.error('PROJECT', `Failed to get project ${projectId}`, error);
            throw error;
        }
    }

    async updateProject(projectId, updates) {
        try {
            const response = await fetch(`/api/projects/${projectId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(updates)
            });

            if (!response.ok) {
                throw new Error('Failed to update project');
            }

            // Update local cache
            const project = this.projects.get(projectId);
            if (project) {
                Object.assign(project, updates);
            }

            Logger.info('PROJECT', `Updated project ${projectId}`, updates);
            return true;
        } catch (error) {
            Logger.error('PROJECT', `Failed to update project ${projectId}`, error);
            throw error;
        }
    }

    async deleteProject(projectId) {
        try {
            const response = await fetch(`/api/projects/${projectId}`, {
                method: 'DELETE'
            });

            if (!response.ok) {
                throw new Error('Failed to delete project');
            }

            this.projects.delete(projectId);
            this.orderedProjects = this.orderedProjects.filter(id => id !== projectId);

            Logger.info('PROJECT', `Deleted project ${projectId}`);
            return true;
        } catch (error) {
            Logger.error('PROJECT', `Failed to delete project ${projectId}`, error);
            throw error;
        }
    }

    async toggleExpansion(projectId) {
        try {
            const response = await fetch(`/api/projects/${projectId}/toggle-expansion`, {
                method: 'PUT'
            });

            if (!response.ok) {
                throw new Error('Failed to toggle expansion');
            }

            const data = await response.json();

            // Update local cache
            const project = this.projects.get(projectId);
            if (project) {
                project.is_expanded = data.is_expanded;
            }

            Logger.info('PROJECT', `Toggled expansion for project ${projectId}`, data.is_expanded);
            return data.is_expanded;
        } catch (error) {
            Logger.error('PROJECT', `Failed to toggle expansion for ${projectId}`, error);
            throw error;
        }
    }

    async reorderProjects(projectIds) {
        try {
            Logger.debug('PROJECT', `Attempting to reorder projects: ${JSON.stringify(projectIds)}`);

            const response = await fetch('/api/projects/reorder', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ project_ids: projectIds })
            });

            if (!response.ok) {
                const errorText = await response.text();
                Logger.error('PROJECT', `Reorder API failed with status ${response.status}: ${errorText}`);
                throw new Error(`Failed to reorder projects: ${response.status}`);
            }

            this.orderedProjects = projectIds;
            Logger.info('PROJECT', 'Reordered projects', projectIds);
            return true;
        } catch (error) {
            Logger.error('PROJECT', 'Failed to reorder projects', error);
            throw error;
        }
    }

    async reorderSessionsInProject(projectId, sessionIds) {
        try {
            const response = await fetch(`/api/projects/${projectId}/sessions/reorder`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_ids: sessionIds })
            });

            if (!response.ok) {
                throw new Error('Failed to reorder sessions');
            }

            Logger.info('PROJECT', `Reordered sessions in project ${projectId}`, sessionIds);
            return true;
        } catch (error) {
            Logger.error('PROJECT', `Failed to reorder sessions in ${projectId}`, error);
            throw error;
        }
    }

    formatPath(absolutePath) {
        if (!absolutePath) return '';

        // Split path by forward or backward slashes
        const parts = absolutePath.split(/[/\\]/).filter(p => p);

        if (parts.length === 0) return '/';
        if (parts.length === 1) return `/${parts[0]}`;
        if (parts.length === 2) return `/${parts.join('/')}`;

        // 3+ folders: show ellipsis + last 2
        return `.../${parts.slice(-2).join('/')}`;
    }

    getProject(projectId) {
        return this.projects.get(projectId);
    }

    getAllProjects() {
        return this.orderedProjects.map(id => this.projects.get(id)).filter(p => p);
    }
}
