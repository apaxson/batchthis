"""
  Copyright 2021 Stones River Meadery (aaron@stonesrivermead.com)

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at

       http://www.apache.org/licenses/LICENSE-2.0

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.
"""
from apps.batchthis.models import Batch


def nav_context(request):
    """
    Supplies the sidebar (batchthis/sidebar.html) with the current active batches on
    every page, since the sidebar is shared via base.html rather than passed per-view.
    """
    if not request.user.is_authenticated:
        return {}
    return {
        'nav_active_batches': Batch.objects.filter(active=True).order_by('-startdate')[:8],
    }
